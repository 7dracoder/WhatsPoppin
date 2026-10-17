"""F5. Link finder: build one company's links from SEC filings and store them.

    result = await build_links("NVDA")                  # opens a session on DATABASE_URL
    links = await read_links(session, "NVDA")           # what the graph shows (F9)
    run = await get_link_run(session, "NVDA")           # running / done / error (F9 polls it)

One run, for the searched company S:
  1. Skip if `graph_link_runs` says the last run is `done` and newer than GRAPH_LINK_TTL_DAYS
     (no network call, not even the company directory when the ticker is given exactly).
     Otherwise mark it `running` and commit, so a poller sees it.
  2. One `list_filings` call (annual reports and 8-Ks) gives S's SIC code, saved to
     `graph_company_profiles`, its ANNUAL_REPORTS newest annual reports and its 8-Ks from the last 90 days.
     Annual reports are ANNUAL_FORMS: 10-K, plus 20-F and 40-F from foreign companies listed
     in the US (TSMC, for example), which never file a 10-K.
  3. Own annual report: `trim_by_phrases`, then the companies named in each chunk (US-listed names
     found through the company directory, as F7 does for markets) are the subjects passed to
     `extract`, with S as the filer.
  4. Reverse lookup: `full_text_search` for S's short name in quotes, annual reports of the last 18
     months; one filing per other filer, the 10 largest filers first (size = position in SEC's
     ticker list, which is ordered by company size). Each is trimmed with `trim_by_name` and
     read with the hit's company as the filer and S as the subject.
  5. 8-Ks: read, and kept only when they announce a material agreement or an acquisition
     (Item 1.01 or 2.01); same trim and extract as the own 10-K.
  6. A filing already in `graph_processed_filings` is skipped; each filing read is recorded.
  7. At most GRAPH_MAX_LINKED linked companies per run; suppliers and customers displace
     partners, competitors and sector peers that this run inserted.
  8. Industry peers: SEC's company list for S's SIC code, largest first, as `sector_peer`
     links: INDUSTRY_PEERS of them next to filing links, or up to GRAPH_MAX_LINKED when S has
     no filing link. If that list fails and S has no filing link, companies already in
     `graph_company_profiles` with S's SIC code are used instead.
  9. Mark the run `done`, or `error` with the message.

Direction (company_graph.md): a filing link is stored with the FILER as `entity_symbol`, and
`relationship_type` is the other company's role for the filer. So S's own filings give rows
(S -> X, type = X's role for S), and reverse hits give rows (filer X -> S, type = S's role for
X). Sector links are (S -> peer, sector_peer). `read_links` reads both directions and flips the
second with `reverse_type`, so every link comes back as "X is S's <type>". The same rows serve
X's graph too, and a link found from both sides is one row, not two.

Every evidence URL is a document fetched by this run's one SecClient (`fetched_urls`): industry
peers cite SEC's industry company list, and the profile fallback cites S's submissions JSON,
both fetched in the same run.

Timing: documents are read in parallel (SEC requests share the client's limiter; model calls
run in threads, at most MAX_PARALLEL_MODEL_CALLS at a time). Only the main coroutine touches
the database, and it commits after each filing, so when the time budget (60 s) runs out the
unfinished reads are cancelled and everything already saved stays. Unfinished filings are not
recorded as read, so the next run tries them again.

build_links commits (it is a run, not a save function): status rows must be visible while the
run is going. The `session` it gets must therefore not be inside a caller's open transaction
that the caller wants to roll back.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import or_, select

from backend.models.entity import Entity
from backend.models.entity_relationship import EntityRelationship
from backend.models.graph_company_profile import GraphCompanyProfile
from backend.models.graph_link_run import GraphLinkRun
from backend.models.graph_processed_filing import GraphProcessedFiling
from company_graph import config as graph_config
from company_graph.companies import Company, CompanyDirectory, get_directory, normalize_name, short_name
from company_graph.extract import RelationshipRejected, extract, reverse_type, save_relationship
from company_graph.market_events import find_companies
from company_graph.sec import SUBMISSIONS_URL, SearchHit, SecClient
from company_graph.trim import DEFAULT_PHRASES, Chunk, trim_by_name, trim_by_phrases

log = logging.getLogger(__name__)

TIME_BUDGET_S = 60.0
ANNUAL_FORMS = ("10-K", "20-F", "40-F")  # US annual report; foreign and Canadian issuers' equivalents
REVERSE_LOOKBACK_DAYS = 548        # 18 months
EIGHT_K_LOOKBACK_DAYS = 90
REVERSE_TOP_HITS = 10
INDUSTRY_PEERS = 3                 # largest same-industry companies added next to filing links
INDUSTRY_PEERS_GRACE_S = 10.0      # time allowed for the industry list even after the run's budget
MAX_EIGHT_KS = 10                  # most recent 8-Ks read per run
ANNUAL_REPORTS = 2                 # newest annual reports read per run: last year's names partners this year's dropped
MAX_SUBJECTS_PER_FILING = 12       # companies asked about per own filing (most mentioned first)
MAX_CHUNKS_PER_PAIR = 2            # passages sent to the model per (filer, subject) pair
MAX_PARALLEL_MODEL_CALLS = 8
RUNNING_STALE_S = 600              # a 'running' row older than this is a crashed run and is restarted

# Lower is preferred when GRAPH_MAX_LINKED is reached.
TYPE_PRIORITY = {"supplier": 0, "customer": 0, "partner": 1, "competitor": 2, "sector_peer": 3}

EIGHT_K_PHRASES = tuple(DEFAULT_PHRASES) + ("agreement", "acquisition", "acquire", "merger")
_ANNOUNCES_DEAL = re.compile(
    r"item\s*(?:1\.01|2\.01)|entry\s+into\s+a\s+material\s+definitive\s+agreement|completion\s+of\s+acquisition",
    re.IGNORECASE,
)


# --- results ----------------------------------------------------------------------

@dataclass
class LinkRunResult:
    symbol: str
    status: str                     # 'running' | 'done' | 'error'
    skipped: bool = False           # True: nothing was fetched (fresh run, run in progress, or fake mode)
    linked: list[str] = field(default_factory=list)   # companies linked by this run
    filings_read: int = 0
    filings_skipped: int = 0        # already in graph_processed_filings
    errors: list[str] = field(default_factory=list)   # per-document failures (the run may still be done)
    timed_out: bool = False
    sector_fallback: bool = False
    error: str | None = None        # the run-level error when status is 'error'


@dataclass(frozen=True)
class StoredLink:
    """One link as S's graph shows it: `symbol` is the other company, `type` its role for S."""
    symbol: str
    name: str
    type: str
    summary: str | None
    evidence_url: str | None
    source: str
    last_confirmed_at: datetime | None


@dataclass(frozen=True)
class _Job:
    key: str               # graph_processed_filings.accession_number
    accession: str
    cik: int
    form: str
    url: str
    kind: str              # 'own10k' | 'own8k' | 'reverse'
    filer: Company


@dataclass(frozen=True)
class _Found:
    entity: Company        # the filer
    related: Company       # the subject
    type: str              # the subject's role for the filer
    summary: str
    other: Company         # the company that is not S


@dataclass
class _DocResult:
    job: _Job
    found: list[_Found] = field(default_factory=list)
    error: str | None = None


# --- small pure helpers ----------------------------------------------------------------

def _norm_symbol(symbol: str) -> str:
    return (symbol or "").strip().lstrip("$").upper()


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def is_fresh(run, now: datetime, ttl_s: float) -> bool:
    """A done run newer than the TTL: reuse its links."""
    fetched = _utc(getattr(run, "fetched_at", None))
    return run is not None and run.status == "done" and fetched is not None and (now - fetched).total_seconds() < ttl_s


def is_in_progress(run, now: datetime) -> bool:
    fetched = _utc(getattr(run, "fetched_at", None))
    return (run is not None and run.status == "running" and fetched is not None
            and (now - fetched).total_seconds() < RUNNING_STALE_S)


def _same_company(a: Company, b: Company) -> bool:
    if a.symbol.strip().upper() == b.symbol.strip().upper():
        return True
    na, nb = normalize_name(a.name), normalize_name(b.name)
    return bool(na) and na == nb


def _name_variants(company: Company) -> list[str]:
    out = [company.name]
    short = short_name(company.name)
    if short and short.lower() != company.name.lower():
        out.append(short)
    return out


def own_subjects(chunks: list[Chunk], filer: Company, directory: CompanyDirectory,
                 max_subjects: int = MAX_SUBJECTS_PER_FILING,
                 max_chunks: int = MAX_CHUNKS_PER_PAIR) -> list[tuple[Company, list[Chunk]]]:
    """Companies named in a filer's own chunks, most mentioned first, each with its first chunks. Pure."""
    seen: dict[str, tuple[Company, list[Chunk]]] = {}
    for chunk in chunks:
        for match in find_companies(chunk.text, directory):
            if _same_company(match.company, filer):
                continue
            entry = seen.setdefault(match.company.symbol, (match.company, []))
            entry[1].append(chunk)
    ranked = sorted(seen.values(), key=lambda e: -len(e[1]))  # stable: ties keep first-mention order
    return [(c, chs[:max_chunks]) for c, chs in ranked[:max_subjects]]


def filer_company(hit: SearchHit, directory: CompanyDirectory) -> Company:
    """The company behind a full-text-search hit. No known ticker: its name is the symbol."""
    for ticker in hit.tickers:
        c = directory.get(ticker) or directory.get(ticker.replace(".", "-"))
        if c is not None:
            return c
    name = hit.company_name.strip() or f"CIK {hit.cik}"
    return Company(symbol=name, name=name, cik=hit.cik)


def reverse_hits(hits: list[SearchHit], company: Company, limit: int = REVERSE_TOP_HITS,
                 size_rank: Callable[[int], int] | None = None) -> list[SearchHit]:
    """Filings by other companies to read, one per filer, at most `limit`. Pure.

    SEC ranks hits by how much the text matches, which favours small companies whose business
    depends on `company`. With `size_rank` (CIK -> 0 for the largest), the largest filers come
    first and search order breaks ties.
    """
    first_per_filer: list[SearchHit] = []
    seen: set[int] = set()
    for hit in hits:
        if hit.cik == company.cik or hit.cik in seen:
            continue
        seen.add(hit.cik)
        first_per_filer.append(hit)
    if size_rank is not None:
        first_per_filer.sort(key=lambda h: size_rank(h.cik))  # stable: search order breaks ties
    return first_per_filer[:limit]


# --- database helpers ----------------------------------------------------------------

async def get_link_run(session, symbol: str):
    """The `graph_link_runs` row for a ticker, or None (F9 reports its status)."""
    return await session.get(GraphLinkRun, _norm_symbol(symbol))


async def _set_run(session, symbol: str, status: str, at: datetime, error: str | None = None) -> None:
    run = await session.get(GraphLinkRun, symbol)
    if run is None:
        run = GraphLinkRun(symbol=symbol)
        session.add(run)
    run.status = status
    run.fetched_at = at
    run.error = error[:1000] if error else None
    await session.flush()
    await session.commit()


async def _save_profile(session, symbol: str, cik: int, sic_code: str | None,
                        sic_description: str | None = None, listing_venue: str | None = None) -> None:
    row = await session.get(GraphCompanyProfile, symbol)
    if row is None:
        row = GraphCompanyProfile(symbol=symbol, cik=cik)
        session.add(row)
    row.cik = cik
    if sic_code:
        row.sic_code = sic_code
    if sic_description:
        row.sic_description = sic_description
    if listing_venue:
        row.listing_venue = listing_venue
    await session.flush()


async def read_links(session, symbol: str, max_linked: int | None = None) -> list[StoredLink]:
    """S's links, as S's graph shows them: rows where S is the entity, plus rows where S is the
    related company with the type flipped by `reverse_type`. One link per (company, type), the
    filing one over the sector one, then the newest. With `max_linked`, only that many companies
    are kept, suppliers and customers first. Read-only.
    """
    s = _norm_symbol(symbol)
    rows = (await session.execute(select(EntityRelationship).where(
        or_(EntityRelationship.entity_symbol == s, EntityRelationship.related_entity_symbol == s)))).scalars().all()

    best: dict[tuple[str, str], EntityRelationship] = {}
    for r in rows:
        if r.entity_symbol == s:
            other, rtype = r.related_entity_symbol, r.relationship_type
        else:
            other, rtype = r.entity_symbol, reverse_type(r.relationship_type)
        if other == s:
            continue
        k = (other, rtype)
        cur = best.get(k)
        if cur is None or _rank_row(r) < _rank_row(cur):
            best[k] = r

    names = {}
    others = sorted({k[0] for k in best})
    if others:
        names = {e.symbol: e.name for e in (await session.execute(
            select(Entity).where(Entity.symbol.in_(others)))).scalars().all()}

    links = [StoredLink(symbol=other, name=names.get(other, other), type=rtype, summary=r.summary,
                        evidence_url=r.evidence_url, source=r.source, last_confirmed_at=_utc(r.last_confirmed_at))
             for (other, rtype), r in best.items()]

    def company_rank(sym: str):
        mine = [l for l in links if l.symbol == sym]
        return (min(TYPE_PRIORITY.get(l.type, 9) for l in mine),
                0 if any(l.source == "filing" for l in mine) else 1, sym)

    order = sorted({l.symbol for l in links}, key=company_rank)
    if max_linked is not None:
        order = order[:max_linked]
    pos = {sym: i for i, sym in enumerate(order)}
    kept = [l for l in links if l.symbol in pos]
    kept.sort(key=lambda l: (pos[l.symbol], TYPE_PRIORITY.get(l.type, 9), l.type))
    return kept


def _rank_row(r) -> tuple:
    ts = _utc(r.last_confirmed_at)
    return (0 if r.source == "filing" else 1, -(ts.timestamp() if ts else 0))


@asynccontextmanager
async def _open_session(database_url: str):
    """A session on the shared database, with this feature's tables created."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from company_graph.db import create_tables, make_engine

    if not database_url:
        raise RuntimeError("DATABASE_URL is not set; the link finder stores links in the shared database")
    engine = make_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(create_tables)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


# --- the run ----------------------------------------------------------------------

async def build_links(
    symbol: str,
    *,
    session=None,
    sec: SecClient | None = None,
    cfg: graph_config.Config | None = None,
    directory: CompanyDirectory | None = None,
    complete_fn: Callable | None = None,
    time_budget_s: float = TIME_BUDGET_S,
    now: datetime | None = None,
) -> LinkRunResult:
    """Find and store `symbol`'s links from SEC filings (see the module docstring).

    `session`: a SQLAlchemy AsyncSession (default: one on DATABASE_URL). build_links commits.
    `sec`: the run's one SecClient (default: a new one, closed at the end).
    `directory`: the company directory (default: companies.get_directory()).
    `complete_fn`: passed to extract (default: the real model). `time_budget_s`: when to stop
    reading. `now`: the clock for TTL and date windows (default: the current UTC time).
    A failed run does not raise: the result (and `graph_link_runs`) says `error`. Only a missing
    or unreachable database (when no `session` is given) raises.
    In fake mode (GRAPH_FAKE=1) nothing is read or written.
    """
    cfg = cfg or graph_config.load()
    key = _norm_symbol(symbol)
    if cfg.fake:
        return LinkRunResult(symbol=key, status="done", skipped=True)
    if session is None:
        async with _open_session(cfg.database_url) as own:
            return await build_links(symbol, session=own, sec=sec, cfg=cfg, directory=directory,
                                     complete_fn=complete_fn, time_budget_s=time_budget_s, now=now)

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
        return LinkRunResult(symbol=key, status="error", error=msg)
    if company.symbol != key:
        skip = await _skip_result(session, company.symbol, started, cfg)
        if skip is not None:
            return skip

    await _set_run(session, company.symbol, "running", started)
    result = LinkRunResult(symbol=company.symbol, status="running")
    own_client = sec is None
    try:
        if own_client:
            sec = SecClient(cfg.sec_contact_email or None)
        run = _LinkRun(session, sec, cfg, directory, company, complete_fn, time_budget_s, started, result)
        await run.run()
    except Exception as exc:  # noqa: BLE001 - any failure ends the run as 'error'
        log.exception("link run for %s failed", company.symbol)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        result.status, result.error = "error", f"{type(exc).__name__}: {exc}"
        await _set_run(session, company.symbol, "error", _utc(clock()), result.error)
        return result
    finally:
        if own_client and sec is not None:
            await sec.aclose()

    if result.error:
        result.status = "error"
        await _set_run(session, company.symbol, "error", _utc(clock()), result.error)
    else:
        result.status = "done"
        await _set_run(session, company.symbol, "done", _utc(clock()))
    return result


async def _skip_result(session, symbol: str, now: datetime, cfg) -> LinkRunResult | None:
    run = await session.get(GraphLinkRun, symbol)
    if is_fresh(run, now, cfg.link_ttl_s):
        return LinkRunResult(symbol=symbol, status="done", skipped=True)
    if is_in_progress(run, now):
        return LinkRunResult(symbol=symbol, status="running", skipped=True)
    return None


class _LinkRun:
    def __init__(self, session, sec, cfg, directory, company, complete_fn, budget_s, started, result):
        self.session = session
        self.sec = sec
        self.cfg = cfg
        self.directory = directory
        self.company = company
        self.complete_fn = complete_fn
        self.budget_s = budget_s
        self.today: date = started.date()
        self.now = started
        self.result = result
        self.fetched_urls: set[str] = set()
        self.queued: set[str] = set()  # processed-filing keys already scheduled in this run
        self.linked: dict[str, list[tuple[EntityRelationship, bool]]] = {}  # other symbol -> (row, inserted)
        self.filing_links_saved = 0  # links from filings this run (industry peers not counted)
        self.sic_code: str | None = None
        self.sic_description: str | None = None
        self.model_slots = asyncio.Semaphore(MAX_PARALLEL_MODEL_CALLS)

    # ---- orchestration ----

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.budget_s
        remaining = lambda: deadline - loop.time()  # noqa: E731

        pending: set[asyncio.Task] = set()
        try:
            try:
                filings = await asyncio.wait_for(
                    self.sec.list_filings(self.company.cik, forms=[*ANNUAL_FORMS, "8-K"]), max(remaining(), 0))
            except TimeoutError:
                self.result.timed_out = True
                filings = None
            if filings is not None:
                self.fetched_urls.add(SUBMISSIONS_URL.format(cik=int(self.company.cik)))
                self.sic_code, self.sic_description = filings.sic_code, filings.sic_description
                await _save_profile(self.session, self.company.symbol, self.company.cik, filings.sic_code,
                                    filings.sic_description, filings.listing_venue)
                await self.session.commit()
                for job in await self._own_jobs(filings.filings):
                    pending.add(asyncio.create_task(self._read(job)))
                search = asyncio.create_task(self._search())
                search.set_name("search")
                pending.add(search)

            while pending:
                if remaining() <= 0:
                    self.result.timed_out = True
                    break
                done, pending = await asyncio.wait(pending, timeout=remaining(),
                                                   return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task.get_name() == "search":
                        pending |= await self._on_search(task)
                    else:
                        await self._on_doc(task.result())
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        await self._industry_peers(remaining)
        if self.result.errors and not self.filing_links_saved:
            # No filing link saved and something failed (SEC, search or the model): report it, so
            # the result is not reused for GRAPH_LINK_TTL_DAYS. Industry peers saved this run are
            # still shown; the error only makes a later search retry.
            self.result.error = (f"no filing links saved; {len(self.result.errors)} failure(s), "
                                 f"first: {self.result.errors[0]}")


    async def _own_jobs(self, filings) -> list[_Job]:
        jobs: list[_Job] = []
        annual = [f for f in filings if f.form.upper() in ANNUAL_FORMS and f.url]
        for f in annual[:ANNUAL_REPORTS]:  # newest first
            jobs.append(_Job(f.accession_number, f.accession_number, self.company.cik, f.form, f.url,
                             "own10k", self.company))
        since = self.today - timedelta(days=EIGHT_K_LOOKBACK_DAYS)
        eight_ks = [f for f in filings if f.form.upper() == "8-K" and f.url and f.filing_date >= since]
        for f in eight_ks[:MAX_EIGHT_KS]:
            jobs.append(_Job(f.accession_number, f.accession_number, self.company.cik, f.form, f.url,
                             "own8k", self.company))
        return [j for j in jobs if await self._claim(j)]

    async def _claim(self, job: _Job) -> bool:
        """True if the filing should be read now: not read before, not already scheduled."""
        if job.key in self.queued:
            return False
        self.queued.add(job.key)
        if await self.session.get(GraphProcessedFiling, job.key) is not None:
            self.result.filings_skipped += 1
            return False
        return True

    async def _search(self) -> list[SearchHit]:
        query = f'"{short_name(self.company.name) or self.company.name}"'
        since = self.today - timedelta(days=REVERSE_LOOKBACK_DAYS)
        return await self.sec.full_text_search(query, forms=list(ANNUAL_FORMS), since=since)

    async def _on_search(self, task: asyncio.Task) -> set[asyncio.Task]:
        try:
            hits = task.result()
        except Exception as exc:  # noqa: BLE001 - the reverse lookup is optional
            self.result.errors.append(f"full-text search: {type(exc).__name__}: {exc}")
            return set()
        new: set[asyncio.Task] = set()
        for hit in reverse_hits(hits, self.company, size_rank=self.directory.size_rank):
            filer = filer_company(hit, self.directory)
            if hit.sic_code and filer.symbol != filer.name:  # a profile needs a ticker
                await _save_profile(self.session, filer.symbol, hit.cik, hit.sic_code)
            # A reverse read is about S only, so it is keyed per subject: the same 10-K can still
            # be read for another company's graph.
            job = _Job(f"{hit.accession_number}#{self.company.symbol}", hit.accession_number, hit.cik,
                       hit.form or "10-K", hit.url, "reverse", filer)
            if await self._claim(job):
                new.add(asyncio.create_task(self._read(job)))
        await self.session.commit()
        return new

    # ---- one document (no database access here) ----

    async def _read(self, job: _Job) -> _DocResult:
        try:
            text = await self.sec.fetch_text(job.url)
            self.fetched_urls.add(job.url)
            if job.kind == "own8k" and not _ANNOUNCES_DEAL.search(text):
                return _DocResult(job)
            if job.kind == "reverse":
                chunks = await asyncio.to_thread(trim_by_name, text, _name_variants(self.company))
                pairs = [(job.filer, self.company, chunks[:MAX_CHUNKS_PER_PAIR])] if chunks else []
            else:
                phrases = EIGHT_K_PHRASES if job.kind == "own8k" else DEFAULT_PHRASES
                chunks = await asyncio.to_thread(trim_by_phrases, text, phrases)
                subjects = await asyncio.to_thread(own_subjects, chunks, self.company, self.directory)
                pairs = [(self.company, subject, chs) for subject, chs in subjects]
            found_lists = await asyncio.gather(*(self._ask(filer, subject, chs) for filer, subject, chs in pairs))
            return _DocResult(job, [f for lst in found_lists for f in lst])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad document does not end the run
            return _DocResult(job, error=f"{job.url}: {type(exc).__name__}: {exc}")

    async def _ask(self, filer: Company, subject: Company, chunks: list[Chunk]) -> list[_Found]:
        other = subject if filer.symbol == self.company.symbol else filer
        out: dict[str, _Found] = {}
        for chunk in chunks:
            async with self.model_slots:
                rels = await asyncio.to_thread(extract, chunk, filer, subject, self.complete_fn)
            for rel in rels:
                out.setdefault(rel.type, _Found(filer, subject, rel.type, rel.summary, other))
        return list(out.values())

    # ---- saving (main coroutine only) ----

    async def _on_doc(self, doc: _DocResult) -> None:
        if doc.error:
            self.result.errors.append(doc.error)
            log.warning("link run %s: %s", self.company.symbol, doc.error)
            return
        self.result.filings_read += 1
        for found in doc.found:
            await self._save(found, doc.job.url)
        self.session.add(GraphProcessedFiling(accession_number=doc.job.key, cik=doc.job.cik,
                                              form=doc.job.form, processed_at=self.now))
        await self.session.flush()
        await self.session.commit()

    async def _save(self, found: _Found, url: str) -> None:
        other = found.other.symbol
        if other not in self.linked and len(self.linked) >= self.cfg.graph_max_linked:
            if TYPE_PRIORITY.get(found.type, 9) != 0 or not await self._displace():
                return
        try:
            row = await save_relationship(self.session, found.entity, found.related, found.type,
                                          found.summary, url, self.fetched_urls)
        except RelationshipRejected as exc:
            log.info("link run %s: rejected %s: %s", self.company.symbol, other, exc)
            return
        inserted = sa_inspect(row).pending
        await self.session.flush()
        self.linked.setdefault(other, []).append((row, inserted))
        self.filing_links_saved += 1
        if other not in self.result.linked:
            self.result.linked.append(other)

    async def _displace(self) -> bool:
        """Drop the least preferred company this run added that has no supplier or customer link."""
        candidates = []
        for sym, rows in self.linked.items():
            if not all(inserted for _, inserted in rows):
                continue  # it also re-confirmed an older row; never delete those
            best = min(TYPE_PRIORITY.get(r.relationship_type, 9) for r, _ in rows)
            if best > 0:
                candidates.append((-best, sym))
        if not candidates:
            return False
        _, sym = sorted(candidates)[0]  # the worst-ranked type goes first
        for row, _ in self.linked.pop(sym):
            await self.session.delete(row)
        await self.session.flush()
        self.result.linked.remove(sym)
        return True

    async def _industry_peers(self, remaining: Callable[[], float]) -> None:
        """The largest companies SEC files under S's industry code, as `sector_peer` links.

        INDUSTRY_PEERS of them next to filing links, or up to GRAPH_MAX_LINKED when S has no
        filing link. If SEC's industry list fails, an S without filing links falls back to
        companies already profiled with the same code.
        """
        if not self.sic_code:
            return
        s = self.company.symbol
        has_links = bool(self.result.linked) or (await self.session.execute(select(EntityRelationship.id).where(
            or_(EntityRelationship.entity_symbol == s, EntityRelationship.related_entity_symbol == s),
            EntityRelationship.source == "filing").limit(1))).first() is not None
        want = INDUSTRY_PEERS if has_links else self.cfg.graph_max_linked
        try:
            url, ciks = await asyncio.wait_for(self.sec.ciks_by_sic(self.sic_code),
                                               max(remaining(), INDUSTRY_PEERS_GRACE_S))
        except Exception as exc:  # noqa: BLE001 - peers are optional
            log.info("link run %s: industry list for SIC %s failed: %s", s, self.sic_code, exc)
            if not has_links:
                await self._sector_fallback()
            return
        self.fetched_urls.add(url)
        peers = sorted({c.cik: c for c in (self.directory.by_cik(k) for k in ciks)
                        if c is not None and not _same_company(c, self.company)}.values(),
                       key=lambda c: self.directory.size_rank(c.cik))
        industry = f"{self.sic_code} ({self.sic_description})" if self.sic_description else self.sic_code
        added = 0
        for peer in peers:
            if added >= want or len(self.linked) >= self.cfg.graph_max_linked:
                break
            if peer.symbol in self.linked:
                continue
            summary = f"SEC lists {self.company.name} and {peer.name} under the same industry code, {industry}."
            try:
                row = await save_relationship(self.session, self.company, peer, "sector_peer", summary, url,
                                              self.fetched_urls, source="sector")
            except RelationshipRejected:
                continue
            inserted = sa_inspect(row).pending
            await self.session.flush()
            self.linked.setdefault(peer.symbol, []).append((row, inserted))
            self.result.linked.append(peer.symbol)
            added += 1
        if added and not has_links:
            self.result.sector_fallback = True
        await self.session.commit()

    async def _sector_fallback(self) -> None:
        s = self.company.symbol
        has_filing_link = (await self.session.execute(select(EntityRelationship.id).where(
            or_(EntityRelationship.entity_symbol == s, EntityRelationship.related_entity_symbol == s),
            EntityRelationship.source == "filing").limit(1))).first()
        if has_filing_link:
            return
        evidence = SUBMISSIONS_URL.format(cik=int(self.company.cik))
        if not self.sic_code or evidence not in self.fetched_urls:
            return
        peers = (await self.session.execute(
            select(GraphCompanyProfile).where(GraphCompanyProfile.sic_code == self.sic_code,
                                              GraphCompanyProfile.symbol != s)
            .order_by(GraphCompanyProfile.symbol).limit(self.cfg.graph_max_linked))).scalars().all()
        industry = f"{self.sic_code} ({self.sic_description})" if self.sic_description else self.sic_code
        for p in peers:
            known = self.directory.get(p.symbol)
            entity = await self.session.get(Entity, p.symbol)
            name = known.name if known else (entity.name if entity is not None else p.symbol)
            peer = Company(symbol=p.symbol, name=name, cik=p.cik)
            summary = f"SEC lists {self.company.name} and {peer.name} under the same industry code, {industry}."
            try:
                await save_relationship(self.session, self.company, peer, "sector_peer", summary, evidence,
                                        self.fetched_urls, source="sector")
            except RelationshipRejected:
                continue
            await self.session.flush()
            self.result.linked.append(peer.symbol)
            self.result.sector_fallback = True
        await self.session.commit()
