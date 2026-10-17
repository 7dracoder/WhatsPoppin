"""Run the relationship extractor without a model API: dump the passages, answer them by hand
(or with any assistant), load the answers.

The link run (links.build_links) takes a `complete_fn`; this module plugs in two of them:

  dump  -- a recorder: every (filer, subject, passage) the run would have sent to the model is
           written to <out>/<SYMBOL>.json with an empty `answer`, and the run itself happens on a
           throwaway in-memory SQLite database, so nothing reaches the shared database and no
           filing is marked as read. SEC fetches use the on-disk cache (.cache/company_graph).
  load  -- a replayer: the same run again, on the shared database, with `complete_fn` answering
           each passage from the file. Saving, validation, the linked-company cap and
           graph_processed_filings all behave exactly as a model-backed run. A passage with no
           answer makes the run fail rather than silently recording the filing as read.

    uv run python -m company_graph.offline_extract dump NVDA AMD --out /tmp/extract
    # fill in each item's "answer": {"relationships": [{"type": ..., "summary": ...}]} (or [])
    uv run python -m company_graph.offline_extract load NVDA AMD --in /tmp/extract

Answer format and rules are those of company_graph/extract.py (SYSTEM_PROMPT is written into
each dump file as "instructions" so the reader has them).
"""

import argparse
import asyncio
import dataclasses
import hashlib
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.llm.client import LlmError
from company_graph import config as graph_config
from company_graph.companies import get_directory
from company_graph.db import create_tables
from company_graph.extract import SYSTEM_PROMPT, ExtractionResult
from company_graph.links import _open_session, build_links
from company_graph.sec import SecClient

log = logging.getLogger("offline_extract")


def passage_id(user_prompt: str) -> str:
    return hashlib.sha1(user_prompt.encode("utf-8")).hexdigest()[:16]


def _split_prompt(user_prompt: str) -> tuple[str, str, str]:
    """build_user_prompt's layout back into (filer, subject, passage)."""
    head, _, passage = user_prompt.partition('\n\nPassage:\n"""\n')
    lines = dict(line.split(": ", 1) for line in head.split("\n") if ": " in line)
    return lines.get("Filer", ""), lines.get("Subject", ""), passage.rstrip().removesuffix('"""').rstrip()


class _Shim:
    """The AsyncSession calls links.py makes, run on a sync SQLite Session (no aiosqlite here)."""

    def __init__(self, session: Session):
        self.s = session

    async def get(self, model, pk):
        return self.s.get(model, pk)

    def add(self, row):
        self.s.add(row)

    async def scalar(self, stmt):
        return self.s.scalar(stmt)

    async def execute(self, stmt):
        return self.s.execute(stmt)

    async def flush(self):
        self.s.flush()

    async def commit(self):
        self.s.commit()

    async def rollback(self):
        self.s.rollback()

    async def delete(self, row):
        self.s.delete(row)


async def dump(symbols: list[str], out: Path, cfg) -> None:
    out.mkdir(parents=True, exist_ok=True)
    directory = await asyncio.to_thread(get_directory)
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    with engine.begin() as conn:
        create_tables(conn)
    sec = SecClient(cfg.sec_contact_email or None)
    try:
        for symbol in symbols:
            items: dict[str, dict] = {}

            def record(system: str, user: str, response_model, _items=items):
                pid = passage_id(user)
                if pid not in _items:
                    filer, subject, passage = _split_prompt(user)
                    _items[pid] = {"id": pid, "filer": filer, "subject": subject, "passage": passage, "answer": None}
                return ExtractionResult()  # no links: this run only exists to collect the passages

            with Session(engine, expire_on_commit=False) as s:
                result = await build_links(symbol, session=_Shim(s), sec=sec, cfg=cfg, directory=directory,
                                           complete_fn=record)
            path = out / f"{symbol.upper()}.json"
            path.write_text(json.dumps({"symbol": symbol.upper(), "instructions": SYSTEM_PROMPT,
                                        "run": {"status": result.status, "error": result.error,
                                                "filings_read": result.filings_read},
                                        "items": list(items.values())}, indent=1))
            log.info("%s: %d passages from %d filings -> %s", symbol, len(items), result.filings_read, path)
    finally:
        await sec.aclose()


async def load(symbols: list[str], src: Path, cfg) -> int:
    directory = await asyncio.to_thread(get_directory)
    failures = 0
    async with _open_session(cfg.database_url) as session:
        sec = SecClient(cfg.sec_contact_email or None)
        try:
            for symbol in symbols:
                path = src / f"{symbol.upper()}.json"
                data = json.loads(path.read_text())
                answers = {i["id"]: i["answer"] for i in data["items"] if i.get("answer") is not None}
                missing = len(data["items"]) - len(answers)
                if missing:
                    log.warning("%s: %d of %d passages have no answer; the run will stop at the first one",
                                symbol, missing, len(data["items"]))

                def replay(system: str, user: str, response_model, _answers=answers, _symbol=symbol):
                    answer = _answers.get(passage_id(user))
                    if answer is None:
                        raise LlmError(f"offline_extract: no answer for passage {passage_id(user)} ({_symbol})")
                    return ExtractionResult.model_validate(answer)

                result = await build_links(symbol, session=session, sec=sec, cfg=cfg, directory=directory,
                                           complete_fn=replay)
                if result.status == "error":
                    failures += 1
                log.info("%s: %s, linked %s%s", symbol, result.status, ", ".join(result.linked) or "nothing",
                         f" ({result.error})" if result.error else "")
        finally:
            await sec.aclose()
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m company_graph.offline_extract",
                                     description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="write the passages a link run would ask the model about")
    d.add_argument("symbols", nargs="+")
    d.add_argument("--out", type=Path, required=True)
    l = sub.add_parser("load", help="run the link build with answers read from the dump files")
    l.add_argument("symbols", nargs="+")
    l.add_argument("--in", dest="src", type=Path, required=True)
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # TTL 0: an existing fresh or errored run must not short-circuit either phase.
    cfg = dataclasses.replace(graph_config.load(), graph_link_ttl_days=0)
    symbols = [s.upper() for s in args.symbols]
    if args.cmd == "dump":
        asyncio.run(dump(symbols, args.out, cfg))
        return 0
    return 1 if asyncio.run(load(symbols, args.src, cfg)) else 0


if __name__ == "__main__":
    sys.exit(main())
