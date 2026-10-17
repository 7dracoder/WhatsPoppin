"""Board members: one company's directors, from SEC insider ownership filings (Forms 3 and 4).

    result = await build_board("TSLA")                  # opens a session on DATABASE_URL
    seats = await read_board(session, "TSLA")           # what GET /graph/{ticker}/board shows
    run = await get_board_run(session, "TSLA")          # running / done / error

Why these forms: every director and officer must report their holdings (Form 3, on joining) and
each trade or grant (Form 4). The filing is structured XML, so no model is needed, and it names
the person by their own SEC CIK. That number is the same in every company's filings, so one
person on two boards is an exact match on `person_cik`, never a guess from a name.

One run, for the company S:
  1. Skip if `graph_board_runs` says the last run is `done` and newer than GRAPH_BOARD_TTL_DAYS,
     or another run is in progress. Otherwise mark it `running` and commit, so a poller sees it.
  2. One `list_filings` call: S's Forms 3, 4 and their amendments from the last LOOKBACK_DAYS
     (about 15 months, so a director whose only filing is a yearly stock grant is still found).
     The newest GRAPH_BOARD_MAX_FILINGS are read.
  3. Each filing's XML is fetched through the run's one SecClient (`fetch_raw`), in parallel
     under the client's rate limiter. SEC lists the primary document as the rendered view
     (`xslF345X05/form4.xml`); the XML itself is the same file name without that folder
     (`raw_xml_document`).
  4. `parse_ownership` reads the reporting owners. A filing whose issuer is another company is
     dropped: S's filing list also holds the forms S itself files as a shareholder of others.
  5. `pick_directors`: per person CIK the newest filing decides, and only people it marks as a
     director are kept. Someone whose newest filing no longer says director has left the board.
  6. Seats are upserted on (company, person). A stored seat is deleted when this run read a newer
     filing saying the person is not a director, or when its filing is older than the lookback.
     Otherwise it stays, so a director outside this run's newest filings is not lost.
  7. Mark the run `done`, or `error` with the message.

Run status: a company with no such filings (many foreign issuers are exempt) ends `done` with no
seats. A timeout ends `done` with what was read. A run that read no filing and hit any failure
(or ran out of time) ends `error`, so an outage is not cached for the TTL.

build_board commits (it is a run, like build_links): give it its own session.

Limits of the source: a director who has left stays until their last filing ages out of the
lookback, unless a later filing of theirs says otherwise. Filed names are "LAST FIRST MIDDLE"
with no marker for where the surname ends, so `display_name` treats the first word as the
surname ("VAN DYKE ANNA" comes out wrong); `raw_name` keeps the filed spelling.
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select

from backend.models.graph_board_run import GraphBoardRun
from backend.models.graph_board_seat import GraphBoardSeat
from company_graph import config as graph_config
from company_graph.companies import CompanyDirectory, get_directory
from company_graph.links import _norm_symbol, _open_session, _utc, is_fresh, is_in_progress
from company_graph.sec import Filing, SecClient, filing_url

log = logging.getLogger(__name__)

TIME_BUDGET_S = 60.0
OWNERSHIP_FORMS = ("3", "4", "3/A", "4/A")  # initial holdings, changes, and their amendments
LOOKBACK_DAYS = 456                # about 15 months: covers a director's yearly grant with room to spare
DEFAULT_ROLE = "Director"

_TRUE = {"1", "true", "yes", "y"}
_SUFFIXES = {"JR": "Jr", "SR": "Sr", "II": "II", "III": "III", "IV": "IV"}  # not "V": usually a middle initial
# A reporting owner can be a fund or a trust; those names are not "LAST FIRST" and are not reordered.
_ORG_WORDS = {"INC", "LLC", "LP", "LLP", "LTD", "CORP", "CO", "TRUST", "FUND", "PARTNERS", "CAPITAL",
              "HOLDINGS", "GROUP", "MANAGEMENT", "FOUNDATION", "ASSOCIATES", "COMPANY", "ADVISORS"}


# --- results ----------------------------------------------------------------------

@dataclass(frozen=True)
class ReportingOwner:
    """One `reportingOwner` block of an ownership filing."""
    cik: int | None
    raw_name: str
    is_director: bool = False
    is_officer: bool = False
    officer_title: str | None = None


@dataclass(frozen=True)
class OwnershipDoc:
    issuer_cik: int | None
    owners: list[ReportingOwner]


@dataclass(frozen=True)
class Director:
    """A board seat ready to store: the person and the filing it comes from."""
    person_cik: int
    name: str
    raw_name: str
    role: str
    is_officer: bool
    filed_at: date
    evidence_url: str


@dataclass
class BoardRunResult:
    symbol: str
    status: str                     # 'running' | 'done' | 'error'
    skipped: bool = False           # True: nothing was fetched (fresh run, run in progress, or fake mode)
    members: list[str] = field(default_factory=list)  # display names of the directors found by this run
    filings_read: int = 0
    errors: list[str] = field(default_factory=list)   # per-filing failures (the run may still be done)
    timed_out: bool = False
    error: str | None = None        # the run-level error when status is 'error'


# --- pure helpers -----------------------------------------------------------------

def member_id(person_cik: int) -> str:
    """The API id of a person: 'cik-0001234567' (SEC writes CIKs as 10 digits)."""
    return f"cik-{int(person_cik):010d}"


def raw_xml_document(primary_document: str) -> str | None:
    """The XML file behind a Form 3 / 4 primary document, or None when the filing has none.

    SEC lists 'xslF345X05/wk-form4_1.xml', the XML rendered through a stylesheet (it answers
    with HTML); 'wk-form4_1.xml' in the same folder is the XML itself.
    """
    parts = [p for p in (primary_document or "").split("/") if p]
    if len(parts) > 1 and parts[0].lower().startswith("xsl"):
        parts = parts[1:]
    document = "/".join(parts)
    return document if document.lower().endswith(".xml") else None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(node, name: str):
    return next((c for c in node if _local(c.tag) == name), None) if node is not None else None


def _text(node, name: str) -> str:
    """The text of a child element. Some fields wrap it in <value>."""
    child = _child(node, name)
    if child is None:
        return ""
    value = _child(child, "value")
    return ((value.text if value is not None else child.text) or "").strip()


def _flag(node, name: str) -> bool:
    return _text(node, name).lower() in _TRUE


def _cik(text: str) -> int | None:
    digits = text.strip()
    return int(digits) if digits.isdigit() and int(digits) > 0 else None


def parse_ownership(xml: str) -> OwnershipDoc:
    """The issuer and reporting owners of a Form 3 / 4 ownership document. Pure.

    Missing fields come back empty or False; flags are accepted as 1/0 or true/false. Text that
    is not XML raises ValueError (an error page must not read as "no directors").
    """
    try:
        root = ET.fromstring(xml.strip())
    except ET.ParseError as exc:
        raise ValueError(f"not an ownership XML document: {exc}") from exc
    owners: list[ReportingOwner] = []
    for node in root.iter():
        if _local(node.tag) != "reportingOwner":
            continue
        ident = _child(node, "reportingOwnerId")
        rel = _child(node, "reportingOwnerRelationship")
        owners.append(ReportingOwner(
            cik=_cik(_text(ident, "rptOwnerCik")),
            raw_name=" ".join(_text(ident, "rptOwnerName").split()),
            is_director=_flag(rel, "isDirector"),
            is_officer=_flag(rel, "isOfficer"),
            officer_title=" ".join(_text(rel, "officerTitle").split()) or None,
        ))
    issuer = next((n for n in root.iter() if _local(n.tag) == "issuer"), None)
    return OwnershipDoc(issuer_cik=_cik(_text(issuer, "issuerCik")), owners=owners)


def _title_word(word: str) -> str:
    bare = word.strip(".,").upper()
    if bare in _SUFFIXES:
        return _SUFFIXES[bare] + ("." if word.endswith(".") and bare in ("JR", "SR") else "")
    if word != word.upper():
        return word  # filed in mixed case already: keep the filer's spelling
    parts = re.split(r"([-'])", word.lower())
    out = "".join(p.capitalize() if p not in "-'" else p for p in parts)
    if out.startswith("Mc") and len(out) > 3:
        out = "Mc" + out[2:].capitalize()
    return out


def display_name(raw_name: str) -> str:
    """'DENHOLM ROBYN M' -> 'Robyn M Denholm'. Pure.

    Filed names are surname first; a trailing Jr/Sr/II/III stays last. 'Last, First' is read the
    same way. Names of funds and trusts are title-cased but not reordered.
    """
    raw = " ".join((raw_name or "").split())
    if not raw:
        return ""
    if "," in raw:
        last, _, rest = raw.partition(",")
        words = last.split() + [w for w in rest.replace(",", " ").split()]
        surname_len = len(last.split())
    else:
        words = raw.split()
        surname_len = 1
    if any(w.strip(".,").upper() in _ORG_WORDS for w in words):
        return " ".join(_title_word(w) for w in words)
    suffixes = []
    while len(words) > surname_len + 1 and words[-1].strip(".,").upper() in _SUFFIXES:
        suffixes.insert(0, words.pop())
    ordered = words[surname_len:] + words[:surname_len] + suffixes
    return " ".join(_title_word(w) for w in ordered)


def pick_directors(entries: list[tuple[Filing, ReportingOwner]]) -> tuple[list[Director], set[int]]:
    """One seat per person from (filing, owner) pairs. Pure.

    Per person CIK the newest filing decides (filing date, then accession number). Returns the
    people that filing marks as directors, sorted by name, and the CIKs of the people it does
    not (officers, large shareholders, directors who left). Owners without a CIK are skipped.
    """
    latest: dict[int, tuple[Filing, ReportingOwner]] = {}
    for filing, owner in entries:
        if owner.cik is None:
            continue
        seen = latest.get(owner.cik)
        if seen is None or (filing.filing_date, filing.accession_number) > (seen[0].filing_date, seen[0].accession_number):
            latest[owner.cik] = (filing, owner)
    directors, others = [], set()
    for cik, (filing, owner) in latest.items():
        if not owner.is_director:
            others.add(cik)
            continue
        directors.append(Director(
            person_cik=cik, name=display_name(owner.raw_name) or member_id(cik), raw_name=owner.raw_name,
            role=owner.officer_title or DEFAULT_ROLE, is_officer=owner.is_officer,
            filed_at=filing.filing_date, evidence_url=filing.url,
        ))
    return sorted(directors, key=lambda d: (d.name.lower(), d.person_cik)), others


# --- database helpers ---------------------------------------------------------------

async def get_board_run(session, symbol: str):
    """The `graph_board_runs` row for a ticker, or None (the API reports its status)."""
    return await session.get(GraphBoardRun, _norm_symbol(symbol))


async def _set_run(session, symbol: str, status: str, at: datetime, error: str | None = None) -> None:
    run = await session.get(GraphBoardRun, symbol)
    if run is None:
        run = GraphBoardRun(symbol=symbol)
        session.add(run)
    run.status = status
    run.fetched_at = at
    run.error = error[:1000] if error else None
    await session.flush()
    await session.commit()


async def read_board(session, symbol: str) -> list[GraphBoardSeat]:
    """The stored directors of a company, by name. Read-only."""
    rows = (await session.execute(
        select(GraphBoardSeat).where(GraphBoardSeat.company_symbol == _norm_symbol(symbol))
    )).scalars().all()
    return sorted(rows, key=lambda r: (r.name.lower(), r.person_cik))


async def save_board(session, symbol: str, directors: list[Director], not_directors: set[int],
                     oldest_kept: date, now: datetime) -> None:
    """Upsert `directors` as the company's seats and drop the seats this run disproved: people
    whose newest filing says they are not a director, and seats filed before `oldest_kept`.
    Flushes; the caller commits."""
    stored = {r.person_cik: r for r in await read_board(session, symbol)}
    found = {d.person_cik for d in directors}
    gone = [r.id for cik, r in stored.items()
            if cik not in found and (cik in not_directors or r.filed_at < oldest_kept)]
    if gone:
        await session.execute(delete(GraphBoardSeat).where(GraphBoardSeat.id.in_(gone)))
    for d in directors:
        row = stored.get(d.person_cik)
        if row is None:
            row = GraphBoardSeat(company_symbol=symbol, person_cik=d.person_cik)
            session.add(row)
        elif row.filed_at > d.filed_at:
            continue  # a newer filing is already stored (this run read fewer filings)
        row.name, row.raw_name, row.role, row.is_officer = d.name, d.raw_name, d.role, d.is_officer
        row.filed_at, row.evidence_url, row.updated_at = d.filed_at, d.evidence_url, now
    await session.flush()


# --- the run ----------------------------------------------------------------------

async def build_board(
    symbol: str,
    *,
    session=None,
    sec: SecClient | None = None,
    cfg: graph_config.Config | None = None,
    directory: CompanyDirectory | None = None,
    time_budget_s: float = TIME_BUDGET_S,
    lookback_days: int = LOOKBACK_DAYS,
    now: datetime | None = None,
) -> BoardRunResult:
    """Find and store `symbol`'s directors from its Form 3 / 4 filings (see the module docstring).

    Arguments as for links.build_links: `session` (default: one on DATABASE_URL; build_board
    commits), `sec` (the run's one SecClient; default: a new one, closed at the end), `directory`
    (default: companies.get_directory()), `now` (the clock for the TTL and the lookback).
    A failed run does not raise: the result (and `graph_board_runs`) says `error`. Only a missing
    or unreachable database (when no `session` is given) raises.
    In fake mode (GRAPH_FAKE=1) nothing is read or written.
    """
    cfg = cfg or graph_config.load()
    key = _norm_symbol(symbol)
    if cfg.fake:
        return BoardRunResult(symbol=key, status="done", skipped=True)
    if session is None:
        async with _open_session(cfg.database_url) as own:
            return await build_board(symbol, session=own, sec=sec, cfg=cfg, directory=directory,
                                     time_budget_s=time_budget_s, lookback_days=lookback_days, now=now)

    clock = (lambda: now) if now is not None else (lambda: datetime.now(timezone.utc))
    started = _utc(clock())

    skip = await _skip_result(session, key, started, cfg)
    if skip is not None:
        return skip

    if directory is None:
        directory = await asyncio.to_thread(get_directory)
    company = directory.resolve(symbol) or directory.resolve(key)
    if company is None:
        msg = f"unknown company: {symbol!r}"
        await _set_run(session, key, "error", started, msg)
        return BoardRunResult(symbol=key, status="error", error=msg)
    if company.symbol != key:
        skip = await _skip_result(session, company.symbol, started, cfg)
        if skip is not None:
            return skip

    await _set_run(session, company.symbol, "running", started)
    result = BoardRunResult(symbol=company.symbol, status="running")
    own_client = sec is None
    try:
        if own_client:
            sec = SecClient(cfg.sec_contact_email or None)
        await _run(session, sec, cfg, company, result, time_budget_s, lookback_days, started)
    except Exception as exc:  # noqa: BLE001 - any failure ends the run as 'error'
        log.exception("board run for %s failed", company.symbol)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        if own_client and sec is not None:
            await sec.aclose()

    result.status = "error" if result.error else "done"
    await _set_run(session, company.symbol, result.status, _utc(clock()), result.error)
    return result


async def _skip_result(session, symbol: str, now: datetime, cfg) -> BoardRunResult | None:
    run = await session.get(GraphBoardRun, symbol)
    if is_fresh(run, now, cfg.board_ttl_s):
        return BoardRunResult(symbol=symbol, status="done", skipped=True)
    if is_in_progress(run, now):
        return BoardRunResult(symbol=symbol, status="running", skipped=True)
    return None


async def _read_filing(sec: SecClient, cik: int, filing: Filing) -> list[ReportingOwner] | None:
    """The reporting owners of one filing about this company; None when it has no XML or is about
    another issuer. No database access here."""
    document = raw_xml_document(filing.primary_document)
    if document is None:
        return None
    doc = parse_ownership(await sec.fetch_raw(filing_url(cik, filing.accession_number, document)))
    if doc.issuer_cik is not None and doc.issuer_cik != int(cik):
        return None
    return doc.owners


async def _run(session, sec, cfg, company, result: BoardRunResult, budget_s: float,
               lookback_days: int, started: datetime) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_s
    since = started.date() - timedelta(days=lookback_days)

    listed = await asyncio.wait_for(sec.list_filings(company.cik, forms=OWNERSHIP_FORMS, since=since),
                                    max(budget_s, 0.001))
    filings = listed.filings[:cfg.graph_board_max_filings]  # newest first

    entries: list[tuple[Filing, ReportingOwner]] = []
    tasks = {asyncio.create_task(_read_filing(sec, company.cik, f)): f for f in filings}
    try:
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(deadline - loop.time(), 0))
            result.timed_out = bool(pending)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    for task, filing in tasks.items():
        if task.cancelled():
            continue
        exc = task.exception()
        if exc is not None:  # one bad filing does not end the run
            result.errors.append(f"{filing.url}: {type(exc).__name__}: {exc}")
            log.warning("board run %s: %s", company.symbol, result.errors[-1])
            continue
        result.filings_read += 1
        entries.extend((filing, owner) for owner in task.result() or [])

    if filings and not result.filings_read and (result.errors or result.timed_out):
        # Nothing was read and something failed: report it, so the empty board is not reused
        # for GRAPH_BOARD_TTL_DAYS. Seats from earlier runs stay.
        first = result.errors[0] if result.errors else "timed out"
        result.error = f"no ownership filing read; {len(result.errors)} failure(s), first: {first}"
        return
    directors, others = pick_directors(entries)
    await save_board(session, company.symbol, directors, others, since, started)
    await session.commit()
    result.members = [d.name for d in directors]
