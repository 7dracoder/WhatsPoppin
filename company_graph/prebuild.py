"""Pre-build company graphs so the hop slider has a second ring to show.

GET /graph/{ticker} builds one company's direct links on demand; hop 2 is every hop-1
company's own graph, which is empty until each has been built. This runs build_links for a
list of companies sequentially (one SEC client, one model call at a time) and is meant to run
once before a demo; results are reused for GRAPH_LINK_TTL_DAYS. Run from the repo root:

    uv run python -m company_graph.prebuild                 # the seed S&P companies (sp500_top50.json)
    uv run python -m company_graph.prebuild NVDA AMD TSM    # specific tickers
    uv run python -m company_graph.prebuild --neighbors NVDA   # NVDA's stored hop-1 companies
    uv run python -m company_graph.prebuild --force ...     # rebuild even when fresh
    uv run python -m company_graph.prebuild --boards --graph-companies   # directors for every company in a filing edge

Each company that is not fresh costs one link run: SEC reads plus a model pass per filing chunk.
Boards (--boards) come from Form 3 / 4 filings with no model call, so they only cost SEC reads.
"""

import argparse
import asyncio
import dataclasses
import logging
import re
import sys
import time

from dotenv import load_dotenv
from sqlalchemy import or_, select

from backend.entities.loader import load_entities
from company_graph import config as graph_config
from company_graph.companies import get_directory
from company_graph.boards import build_board
from company_graph.links import _open_session, build_links
from backend.models.entity_relationship import EntityRelationship
from company_graph.sec import SecClient

log = logging.getLogger("prebuild")


TICKER = re.compile(r"^[A-Z0-9]{1,6}([.-][A-Z0-9]{1,2})?$")  # the backend keys unlisted foreign companies by name


async def graph_companies(session) -> list[str]:
    """Every ticker that appears in a filing edge: what the workspace can show a node for."""
    r = EntityRelationship
    rows = await session.execute(select(r.entity_symbol, r.related_entity_symbol).where(r.source == "filing"))
    return sorted({s for pair in rows for s in pair if TICKER.match(s)})


async def neighbors_of(session, symbol: str) -> list[str]:
    """Companies linked to `symbol` by a filing edge, in either direction."""
    r = EntityRelationship
    rows = await session.execute(select(r.entity_symbol, r.related_entity_symbol).where(
        r.source == "filing", or_(r.entity_symbol == symbol, r.related_entity_symbol == symbol)))
    return sorted({b if a == symbol else a for a, b in rows})


async def main(args) -> int:
    load_dotenv()
    cfg = graph_config.load()
    if args.force:
        cfg = dataclasses.replace(cfg, graph_link_ttl_days=0, graph_board_ttl_days=0)  # is_fresh is then never true
    directory = await asyncio.to_thread(get_directory)
    failures = 0
    async with _open_session(cfg.database_url) as session:
        symbols = [s.upper() for s in args.tickers]
        if args.graph_companies:
            symbols += await graph_companies(session)
        if args.neighbors:
            for s in args.neighbors:
                symbols += await neighbors_of(session, s.upper())
        if not symbols:
            symbols = [e.symbol for e in load_entities()]
        symbols = list(dict.fromkeys(symbols))  # dedupe, keep order
        log.info("%d companies", len(symbols))
        sec = SecClient(cfg.sec_contact_email or None)
        build = build_board if args.boards else build_links
        what = "board" if args.boards else "links"
        try:
            for i, symbol in enumerate(symbols, 1):
                t0 = time.monotonic()
                result = await build(symbol, session=session, sec=sec, cfg=cfg, directory=directory)
                took = time.monotonic() - t0
                if result.status == "error":
                    failures += 1
                extra = f", {len(result.members)} directors" if args.boards and result.status == "done" else ""
                log.info("[%d/%d] %s %s: %s%s%s in %.0fs", i, len(symbols), symbol, what, result.status,
                         " (fresh, skipped)" if getattr(result, "skipped", False) else "", extra, took)
        finally:
            await sec.aclose()
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m company_graph.prebuild", description=__doc__.split("\n\n")[0])
    parser.add_argument("tickers", nargs="*", help="companies to build (default: the seed S&P list)")
    parser.add_argument("--neighbors", nargs="*", metavar="TICKER", default=[],
                        help="also build every company already linked to these")
    parser.add_argument("--graph-companies", action="store_true",
                        help="also every ticker that appears in a stored filing edge")
    parser.add_argument("--boards", action="store_true",
                        help="build boards of directors (Form 3/4, no model call) instead of links")
    parser.add_argument("--force", action="store_true", help="rebuild companies whose data is still fresh")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        sys.exit(1 if asyncio.run(main(parser.parse_args())) else 0)
    except KeyboardInterrupt:
        pass
