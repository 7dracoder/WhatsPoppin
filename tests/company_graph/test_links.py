"""Tests for company_graph.links (F5). No network: SEC goes to an httpx.MockTransport, the model
is a fake complete_fn, and the database is in-memory SQLite behind a small AsyncSession shim."""

import asyncio
import os
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from backend.llm.client import LlmError
from backend.models.entity_relationship import EntityRelationship
from backend.models.graph_company_profile import GraphCompanyProfile
from backend.models.graph_link_run import GraphLinkRun
from backend.models.graph_processed_filing import GraphProcessedFiling
from company_graph import links
from company_graph.companies import Company, CompanyDirectory
from company_graph.config import Config
from company_graph.db import create_tables
from company_graph.links import build_links, get_link_run, is_fresh, own_subjects, read_links, reverse_hits
from company_graph.sec import BROWSE_URL, SUBMISSIONS_URL, RateLimiter, SearchHit, SecClient, filing_url
from company_graph.trim import Chunk

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
TSLA_CIK = 1318605

DIRECTORY = CompanyDirectory.from_sec_json({str(i): {"cik_str": cik, "ticker": t, "title": n} for i, (cik, t, n) in enumerate([
    (TSLA_CIK, "TSLA", "Tesla, Inc."),
    (1045810, "NVDA", "NVIDIA CORP"),
    (37996, "F", "Ford Motor Co"),
    (1467858, "GM", "General Motors Co"),
    (915913, "ALB", "ALBEMARLE CORP"),
    (555, "PCRFY", "Panasonic Holdings Corp"),
    (320193, "AAPL", "Apple Inc."),
])})

TEN_K = filing_url(TSLA_CIK, "0001628280-26-003952", "tsla-20251231.htm")
OLD_TEN_K = filing_url(TSLA_CIK, "0001628280-25-000001", "tsla-20241231.htm")
DEAL_8K = filing_url(TSLA_CIK, "0001628280-26-020000", "deal.htm")
PLAIN_8K = filing_url(TSLA_CIK, "0001628280-26-020001", "plain.htm")
OLD_8K = filing_url(TSLA_CIK, "0001628280-26-001000", "old8k.htm")
ALB_10K = filing_url(915913, "0000915913-26-000010", "alb-20251231.htm")
PRIVATE_10K = filing_url(123, "0000000123-26-000001", "pvt.htm")
SUBMISSIONS = SUBMISSIONS_URL.format(cik=TSLA_CIK)


def submissions(sic="3711"):
    rows = [
        ("0001628280-26-020000", "2026-09-20", "8-K", "deal.htm"),
        ("0001628280-26-020001", "2026-09-10", "8-K", "plain.htm"),
        ("0001628280-26-003952", "2026-01-29", "10-K", "tsla-20251231.htm"),
        ("0001628280-26-001000", "2026-01-05", "8-K", "old8k.htm"),
        ("0001628280-25-000001", "2025-01-30", "10-K", "tsla-20241231.htm"),
    ]
    return {
        "cik": "0001318605", "name": "Tesla, Inc.", "sic": sic,
        "sicDescription": "Motor Vehicles & Passenger Car Bodies", "tickers": ["TSLA"], "exchanges": ["Nasdaq"],
        "filings": {"recent": {
            "accessionNumber": [r[0] for r in rows], "filingDate": [r[1] for r in rows],
            "reportDate": ["" for _ in rows], "form": [r[2] for r in rows], "primaryDocument": [r[3] for r in rows],
        }},
    }


def efts_hit(adsh, doc, cik, display, sics=("2800",)):
    return {"_id": f"{adsh}:{doc}", "_source": {
        "ciks": [f"{cik:010d}"], "display_names": [display], "form": "10-K", "root_forms": ["10-K"],
        "file_date": "2026-02-10", "sics": list(sics), "adsh": adsh, "file_type": "10-K"}}


EFTS = {"hits": {"total": {"value": 4}, "hits": [
    efts_hit("0001628280-26-003952", "tsla-20251231.htm", TSLA_CIK, "Tesla, Inc.  (TSLA)  (CIK 0001318605)"),
    efts_hit("0000915913-26-000010", "alb-20251231.htm", 915913, "ALBEMARLE CORP  (ALB)  (CIK 0000915913)"),
    efts_hit("0000915913-26-000010", "ex21.htm", 915913, "ALBEMARLE CORP  (ALB)  (CIK 0000915913)"),
    efts_hit("0000000123-26-000001", "pvt.htm", 123, "Some Private Co  (CIK 0000000123)", sics=()),
]}}

DOCS = {
    TEN_K: "<html><body><p>Competition. We compete with Ford Motor and General Motors in electric vehicles.</p>"
           "<p>Panasonic Holdings is the primary supplier of our battery cells.</p>"
           "<p>Tesla designs its own vehicles.</p></body></html>",
    OLD_TEN_K: "<p>We compete with Ford Motor.</p>",
    DEAL_8K: "<p>Item 1.01 Entry into a Material Definitive Agreement. Tesla entered into an agreement with "
             "NVIDIA for computing hardware.</p>",
    PLAIN_8K: "<p>Item 5.02 Departure of Directors. Our director retired. We thank Apple for nothing.</p>",
    OLD_8K: "<p>Item 1.01 agreement with Apple.</p>",
    ALB_10K: "<p>Our lithium customers include Tesla, which signed a supply agreement with us.</p>",
    PRIVATE_10K: "<p>We have a joint development agreement with Tesla, Inc. for charging equipment.</p>",
}

# (filer, subject) -> relationships the fake model reports (the subject's role for the filer)
REPLIES = {
    ("Tesla, Inc.", "Ford Motor Co"): [("competitor", "Tesla names Ford as a competitor in electric vehicles.")],
    ("Tesla, Inc.", "General Motors Co"): [("competitor", "Tesla names General Motors as a competitor.")],
    ("Tesla, Inc.", "Panasonic Holdings Corp"): [("supplier", "Tesla states Panasonic is the primary supplier of its battery cells.")],
    ("Tesla, Inc.", "NVIDIA CORP"): [("partner", "Tesla entered into an agreement with NVIDIA for computing hardware.")],
    ("ALBEMARLE CORP", "Tesla, Inc."): [("customer", "Albemarle names Tesla as a lithium customer.")],
    ("Some Private Co", "Tesla, Inc."): [("partner", "Some Private Co has a joint development agreement with Tesla.")],
}


def industry_feed(ciks):
    entries = "".join(f"<entry><content type=\"text/xml\"><company-info><cik>{c:010d}</cik><sic>3711</sic>"
                      f"</company-info></content></entry>" for c in ciks)
    return f'<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'


class FakeModel:
    def __init__(self, replies=REPLIES, fail=False, delay=0.0):
        self.replies, self.fail, self.delay, self.calls = replies, fail, delay, []

    def __call__(self, system, user, response_model):
        filer = re.search(r"^Filer: (.*)$", user, re.M).group(1)
        subject = re.search(r"^Subject: (.*)$", user, re.M).group(1)
        self.calls.append((filer, subject))
        if self.delay:
            import time
            time.sleep(self.delay)
        if self.fail:
            raise LlmError("model down")
        rels = [{"type": t, "summary": s} for t, s in self.replies.get((filer, subject), [])]
        return response_model.model_validate({"relationships": rels})


class Sec:
    """A MockTransport-backed SecClient factory that records every request."""

    def __init__(self, docs=DOCS, efts=EFTS, subs=None, slow=(), status=None, industry=None):
        self.docs, self.efts, self.subs, self.slow, self.status = docs, efts, subs or submissions(), set(slow), status
        self.industry = industry  # CIKs for SEC's company list by SIC code; None = that request 404s
        self.urls: list[str] = []

    async def handler(self, request):
        url = str(request.url)
        self.urls.append(url)
        if self.status:
            return httpx.Response(self.status)
        if url == SUBMISSIONS:
            return httpx.Response(200, json=self.subs)
        if urlparse(url).netloc == "efts.sec.gov":
            return httpx.Response(200, json=self.efts)
        if url.startswith(BROWSE_URL) and self.industry is not None:
            return httpx.Response(200, text=industry_feed(self.industry))
        if url in self.slow:
            await asyncio.sleep(5)
        if url in self.docs:
            return httpx.Response(200, text=self.docs[url])
        return httpx.Response(404)

    def client(self):
        return SecClient("test@example.com", cache_dir=None, limiter=RateLimiter(rate=10_000),
                         transport=httpx.MockTransport(self.handler))

    def archive_urls(self):
        return [u for u in self.urls if "/Archives/" in u]


class AsyncSessionShim:
    """The AsyncSession calls links.py makes, run on a sync SQLite Session (aiosqlite is not installed)."""

    def __init__(self, session):
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


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    event.listen(eng, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    with eng.begin() as conn:
        create_tables(conn)
    yield eng
    eng.dispose()


def run(engine, sec, *, model=None, cfg=None, now=NOW, budget=60.0, directory=DIRECTORY, symbol="TSLA"):
    async def go():
        with Session(engine, expire_on_commit=False) as s:
            async with sec.client() as client:
                return await build_links(symbol, session=AsyncSessionShim(s), sec=client, cfg=cfg or Config(),
                                         directory=directory, complete_fn=model or FakeModel(),
                                         time_budget_s=budget, now=now)
    return asyncio.run(go())


def rows(engine, model):
    with Session(engine) as s:
        return s.scalars(select(model)).all()


def stored_links(engine, symbol="TSLA", max_linked=None):
    with Session(engine) as s:
        return asyncio.run(read_links(AsyncSessionShim(s), symbol, max_linked))


def link_run(engine, symbol="TSLA"):
    with Session(engine) as s:
        return asyncio.run(get_link_run(AsyncSessionShim(s), symbol))


# --- a full run ---------------------------------------------------------------------

def test_full_run_saves_links_from_every_source(engine):
    sec = Sec()
    result = run(engine, sec)

    assert result.status == "done" and not result.skipped and not result.timed_out and result.error is None
    assert set(result.linked) == {"F", "GM", "PCRFY", "NVDA", "ALB", "Some Private Co"}

    got = {(l.symbol, l.type) for l in stored_links(engine)}
    assert got == {("PCRFY", "supplier"), ("ALB", "supplier"), ("NVDA", "partner"),
                   ("Some Private Co", "partner"), ("F", "competitor"), ("GM", "competitor")}

    # Direction: own filings are stored S -> X; reverse hits are stored filer -> S with the subject's role.
    by_pair = {(r.entity_symbol, r.related_entity_symbol): r for r in rows(engine, EntityRelationship)}
    assert by_pair[("TSLA", "PCRFY")].relationship_type == "supplier"
    assert by_pair[("ALB", "TSLA")].relationship_type == "customer"
    assert by_pair[("ALB", "TSLA")].evidence_url == ALB_10K
    assert all(r.source == "filing" and float(r.confidence) == 0.9 for r in by_pair.values())

    # Every evidence URL is a filing this run downloaded.
    assert {r.evidence_url for r in by_pair.values()} <= set(sec.archive_urls())

    run_row = link_run(engine)
    assert run_row.status == "done" and run_row.error is None

    profile = rows(engine, GraphCompanyProfile)
    by_symbol = {p.symbol: p for p in profile}
    assert by_symbol["TSLA"].sic_code == "3711" and by_symbol["TSLA"].listing_venue == "Nasdaq"
    assert by_symbol["ALB"].sic_code == "2800"  # the reverse filer's SIC, from the search hit


def test_reads_only_the_right_filings(engine):
    sec = Sec()
    model = FakeModel()
    run(engine, sec, model=model)
    read = sec.archive_urls()
    # The two newest annual reports (ANNUAL_REPORTS) are read; an 8-K older than 90 days is not.
    assert sorted(read) == sorted({TEN_K, OLD_TEN_K, DEAL_8K, PLAIN_8K, ALB_10K, PRIVATE_10K})
    assert OLD_8K not in read
    # Own hit and the duplicate accession (exhibit) are not read; the 8-K without a deal is read but not extracted.
    assert ("Tesla, Inc.", "Apple Inc.") not in model.calls
    keys = {r.accession_number for r in rows(engine, GraphProcessedFiling)}
    assert keys == {"0001628280-26-003952", "0001628280-25-000001", "0001628280-26-020000", "0001628280-26-020001",
                    "0000915913-26-000010#TSLA", "0000000123-26-000001#TSLA"}


# --- TTL and repeated runs ----------------------------------------------------------------

def test_second_run_inside_ttl_makes_no_network_calls(engine, monkeypatch):
    run(engine, Sec())
    second = Sec()
    model = FakeModel()

    def no_directory():
        raise AssertionError("the directory must not be loaded inside the TTL")

    monkeypatch.setattr(links, "get_directory", no_directory)
    result = run(engine, second, model=model, now=NOW + timedelta(days=6), directory=None)
    assert result.skipped and result.status == "done"
    assert second.urls == [] and model.calls == []


def test_run_after_ttl_reads_no_filing_twice(engine):
    run(engine, Sec())
    before = len(rows(engine, EntityRelationship))
    again = Sec()
    result = run(engine, again, now=NOW + timedelta(days=8))
    assert result.status == "done" and not result.skipped
    assert again.archive_urls() == []           # every filing was recorded as read
    assert result.filings_skipped == 6
    assert len(rows(engine, EntityRelationship)) == before  # links from the first run stay


def test_reverse_reads_are_per_subject(engine):
    run(engine, Sec())
    # The same Albemarle 10-K is still read for another company's graph.
    nvda_subs = dict(submissions(), cik="0001045810", name="NVIDIA CORP", tickers=["NVDA"])
    nvda_subs["filings"] = {"recent": {k: [] for k in ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")}}
    sec = Sec(subs=nvda_subs)
    sec_url = SUBMISSIONS_URL.format(cik=1045810)

    async def handler(request, inner=sec.handler):
        if str(request.url) == sec_url:
            sec.urls.append(sec_url)
            return httpx.Response(200, json=nvda_subs)
        return await inner(request)

    sec.handler = handler
    run(engine, sec, symbol="NVDA")
    assert ALB_10K in sec.archive_urls()


def test_running_run_is_not_started_twice(engine):
    with Session(engine) as s:
        s.add(GraphLinkRun(symbol="TSLA", status="running", fetched_at=NOW - timedelta(seconds=30)))
        s.commit()
    sec = Sec()
    result = run(engine, sec)
    assert result.skipped and result.status == "running" and sec.urls == []


def test_fake_mode_calls_nothing(engine):
    sec = Sec()
    result = run(engine, sec, cfg=Config(graph_fake=1))
    assert result.skipped and sec.urls == [] and rows(engine, GraphLinkRun) == []


# --- max linked ----------------------------------------------------------------------------

def test_max_linked_prefers_suppliers_and_customers(engine):
    # Only the own 10-K: Ford and GM (competitors) are found before Panasonic (supplier).
    docs = {TEN_K: DOCS[TEN_K]}
    subs = submissions()
    sec = Sec(docs=docs, efts={"hits": {"hits": []}}, subs=subs)
    result = run(engine, sec, cfg=Config(graph_max_linked=2))
    assert result.status == "done"
    assert sorted(result.linked) == ["GM", "PCRFY"]  # the supplier displaced a competitor
    assert {(r.related_entity_symbol, r.relationship_type) for r in rows(engine, EntityRelationship)} == {
        ("GM", "competitor"), ("PCRFY", "supplier")}


def test_partner_does_not_displace_at_the_cap(engine):
    sec = Sec(docs={TEN_K: DOCS[TEN_K]}, efts={"hits": {"hits": []}})
    replies = dict(REPLIES)
    replies[("Tesla, Inc.", "Panasonic Holdings Corp")] = [("partner", "Tesla partners with Panasonic.")]
    result = run(engine, sec, cfg=Config(graph_max_linked=2), model=FakeModel(replies))
    assert sorted(result.linked) == ["F", "GM"]


def test_read_links_caps_and_orders_by_preference(engine):
    run(engine, Sec())
    capped = stored_links(engine, max_linked=3)
    assert len({l.symbol for l in capped}) == 3
    assert [l.type for l in capped][:2] == ["supplier", "supplier"]
    assert all(l.name for l in capped)
    assert {l.symbol: l.name for l in stored_links(engine)}["ALB"] == "ALBEMARLE CORP"


# --- sector fallback -----------------------------------------------------------------------

def _seed_profiles(engine):
    with Session(engine) as s:
        s.add_all([GraphCompanyProfile(symbol="F", cik=37996, sic_code="3711"),
                   GraphCompanyProfile(symbol="GM", cik=1467858, sic_code="3711"),
                   GraphCompanyProfile(symbol="AAPL", cik=320193, sic_code="3571")])
        s.commit()


def test_sector_fallback_when_no_filing_link(engine):
    _seed_profiles(engine)
    sec = Sec()
    result = run(engine, sec, model=FakeModel(replies={}))
    assert result.status == "done" and result.sector_fallback
    saved = rows(engine, EntityRelationship)
    assert {(r.entity_symbol, r.related_entity_symbol, r.relationship_type) for r in saved} == {
        ("TSLA", "F", "sector_peer"), ("TSLA", "GM", "sector_peer")}
    assert all(r.source == "sector" and float(r.confidence) == 0.3 and r.evidence_url == SUBMISSIONS for r in saved)
    assert SUBMISSIONS in sec.urls
    assert {l.name for l in stored_links(engine)} == {"Ford Motor Co", "General Motors Co"}


def test_sector_fallback_can_be_empty(engine):
    result = run(engine, Sec(), model=FakeModel(replies={}))
    assert result.status == "done" and not result.sector_fallback and result.linked == []
    assert rows(engine, EntityRelationship) == [] and link_run(engine).status == "done"


def test_no_sector_fallback_when_filing_links_exist(engine):
    _seed_profiles(engine)
    run(engine, Sec())
    assert all(r.source == "filing" for r in rows(engine, EntityRelationship))


# --- time budget -------------------------------------------------------------------------

def test_stops_at_the_time_budget_and_keeps_what_was_saved(engine):
    sec = Sec(slow={ALB_10K, PRIVATE_10K, DEAL_8K})
    result = run(engine, sec, budget=0.5)
    assert result.timed_out and result.status == "done"
    assert link_run(engine).status == "done"
    assert {"F", "GM", "PCRFY"} <= set(result.linked)        # from the fast own 10-K
    assert {r.related_entity_symbol for r in rows(engine, EntityRelationship)} >= {"F", "GM", "PCRFY"}
    keys = {r.accession_number for r in rows(engine, GraphProcessedFiling)}
    assert "0001628280-26-003952" in keys
    assert "0000915913-26-000010#TSLA" not in keys and "0001628280-26-020000" not in keys  # retried next run


# --- errors -----------------------------------------------------------------------------

def test_sec_failure_marks_error(engine):
    result = run(engine, Sec(status=403))
    assert result.status == "error" and "HTTPStatusError" in result.error
    row = link_run(engine)
    assert row.status == "error" and "403" in row.error
    assert rows(engine, EntityRelationship) == []


def test_model_failure_on_every_filing_marks_error(engine):
    result = run(engine, Sec(), model=FakeModel(fail=True))
    assert result.status == "error" and "model down" in result.error
    assert link_run(engine).status == "error"
    # Only the 8-K that needed no model call counts as read; the others are tried again next run.
    assert {r.accession_number for r in rows(engine, GraphProcessedFiling)} == {"0001628280-26-020001"}


def test_unknown_company_marks_error(engine):
    sec = Sec()
    result = run(engine, sec, symbol="ZZZZ")
    assert result.status == "error" and sec.urls == []
    assert link_run(engine, "ZZZZ").status == "error"


def test_missing_contact_email_marks_error(engine, monkeypatch):
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)

    async def go():
        with Session(engine) as s:
            return await build_links("TSLA", session=AsyncSessionShim(s), cfg=Config(), directory=DIRECTORY, now=NOW)

    result = asyncio.run(go())
    assert result.status == "error" and "SEC_CONTACT_EMAIL" in result.error
    assert link_run(engine).status == "error"


def test_a_run_after_an_error_is_not_skipped(engine):
    run(engine, Sec(status=403))
    assert run(engine, Sec()).status == "done"


# --- pure helpers ---------------------------------------------------------------------------

def test_is_fresh():
    done = GraphLinkRun(symbol="X", status="done", fetched_at=NOW - timedelta(days=1))
    assert is_fresh(done, NOW, 7 * 86400)
    assert not is_fresh(done, NOW + timedelta(days=7), 7 * 86400)
    assert not is_fresh(replace_run(done, status="error"), NOW, 7 * 86400)
    assert is_fresh(replace_run(done, fetched_at=(NOW - timedelta(hours=1)).replace(tzinfo=None)), NOW, 3 * 3600)
    assert not is_fresh(None, NOW, 1)


def replace_run(run_row, **kw):
    vals = dict(symbol=run_row.symbol, status=run_row.status, fetched_at=run_row.fetched_at)
    vals.update(kw)
    return GraphLinkRun(**vals)


def _hit(acc, cik, doc="a.htm"):
    return SearchHit(acc, cik, "X", [], "10-K", date(2026, 1, 1), doc, "u", None)


def test_reverse_hits_skips_own_filings_and_keeps_one_per_filer():
    hits = [_hit("1", TSLA_CIK), _hit("2", 5), _hit("2", 5, "b.htm"), _hit("3", 5)] + \
           [_hit(str(i), 100 + i) for i in range(4, 20)]
    out = reverse_hits(hits, Company("TSLA", "Tesla, Inc.", TSLA_CIK))
    assert len(out) == 10 and out[0].accession_number == "2"  # search order without a size ranking
    assert len({h.cik for h in out}) == 10


def test_reverse_hits_prefers_the_largest_filers():
    tesla = Company("TSLA", "Tesla, Inc.", TSLA_CIK)
    hits = [_hit("small", 900), _hit("big", 37996), _hit("unlisted", 123), _hit("mid", 1467858)]
    out = reverse_hits(hits, tesla, limit=3, size_rank=DIRECTORY.size_rank)
    assert [h.accession_number for h in out] == ["big", "mid", "small"]  # F, GM, then search order


def test_own_subjects_ranks_by_mentions_and_excludes_self():
    chunks = [Chunk(0, "Tesla competes with Ford Motor."), Chunk(10, "Panasonic Holdings and Ford Motor supply.")]
    subjects = own_subjects(chunks, Company("TSLA", "Tesla, Inc.", TSLA_CIK), DIRECTORY)
    assert [c.symbol for c, _ in subjects] == ["F", "PCRFY"]
    assert len(subjects[0][1]) == 2


# --- live check (opt-in, never runs in CI) -----------------------------------------------------

LIVE = all(os.environ.get(k) for k in ("SEC_CONTACT_EMAIL", "OPENROUTER", "DATABASE_URL")) and \
    os.environ.get("GRAPH_LIVE_EVAL") == "1"


@pytest.mark.skipif(not LIVE, reason="live SEC + model + database check; set GRAPH_LIVE_EVAL=1, "
                                     "SEC_CONTACT_EMAIL, OPENROUTER and DATABASE_URL to run it")
def test_live_large_company_gets_three_links():  # pragma: no cover - calls SEC, the model and the database
    from sqlalchemy.ext.asyncio import AsyncSession

    from company_graph.config import load
    from company_graph.db import create_tables as create, make_engine

    cfg = replace(load(), graph_link_ttl_days=0)  # force a fresh run

    async def go():
        engine = make_engine(cfg.database_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(create)
            async with AsyncSession(engine, expire_on_commit=False) as s:
                result = await build_links("NVDA", session=s, cfg=cfg)
                return result, await read_links(s, "NVDA")
        finally:
            await engine.dispose()

    result, stored = asyncio.run(go())
    assert result.status == "done", result.error
    filing = [l for l in stored if l.source == "filing"]
    assert len({l.symbol for l in filing}) >= 3
    assert all(l.evidence_url.startswith("https://www.sec.gov/Archives/edgar/data/") for l in filing)


# --- foreign issuers (20-F, 40-F) -------------------------------------------------

def test_reverse_search_covers_foreign_annual_reports(engine):
    sec = Sec()
    run(engine, sec)
    searches = [httpx.URL(u) for u in sec.urls if urlparse(u).netloc == "efts.sec.gov"]
    assert searches and all(u.params["forms"] == "10-K,20-F,40-F" for u in searches)


def test_foreign_issuer_own_20f_is_read(engine):
    subs = submissions()
    forms = subs["filings"]["recent"]["form"]
    subs["filings"]["recent"]["form"] = ["20-F" if f == "10-K" else f for f in forms]
    sec = Sec(subs=subs)
    result = run(engine, sec)
    assert result.status == "done"
    assert TEN_K in sec.archive_urls()  # the foreign annual form is treated like a 10-K
    assert ("PCRFY", "supplier") in {(l.symbol, l.type) for l in stored_links(engine)}


# --- industry peers --------------------------------------------------------------------------

def test_industry_peers_added_next_to_filing_links_largest_first(engine):
    # Industry list in SEC's (non-size) order; the directory ranks NVDA, F, GM, ALB, PCRFY, AAPL by position.
    sec = Sec(industry=[320193, 1467858, 37996, TSLA_CIK, 999999])
    result = run(engine, sec, cfg=Config(graph_max_linked=12))
    assert result.status == "done" and not result.sector_fallback
    peers = [l for l in stored_links(engine) if l.source == "sector"]
    # F and GM are already on the graph as competitors, TSLA is itself and 999999 is unlisted: AAPL is left.
    assert {l.symbol for l in peers} == {"AAPL"}
    assert all(l.evidence_url.startswith(BROWSE_URL) for l in peers)
    assert any(u.startswith(BROWSE_URL) for u in sec.urls)


EXTRA_PEERS = [(9000 + i, f"PEER{i}", f"Peer Motors {i} Inc") for i in range(1, 6)]  # ranked after DIRECTORY


def _directory_with_peers():
    rows = [(c.cik, c.symbol, c.name) for c in (DIRECTORY.get(t) for t in
            ["TSLA", "NVDA", "F", "GM", "ALB", "PCRFY", "AAPL"])] + EXTRA_PEERS
    return CompanyDirectory.from_sec_json({str(i): {"cik_str": cik, "ticker": t, "title": n}
                                           for i, (cik, t, n) in enumerate(rows)})


def test_industry_peers_capped_at_three_when_filing_links_exist(engine):
    industry = [cik for cik, _, _ in reversed(EXTRA_PEERS)] + [320193]
    run(engine, Sec(industry=industry), cfg=Config(graph_max_linked=20), directory=_directory_with_peers())
    peers = [l.symbol for l in stored_links(engine) if l.source == "sector"]
    assert sorted(peers) == ["AAPL", "PEER1", "PEER2"]  # the three largest by SEC's size order


def test_industry_peers_fill_the_graph_when_no_filing_links(engine):
    sec = Sec(efts={"hits": {"total": {"value": 0}, "hits": []}}, industry=[cik for cik, _, _ in EXTRA_PEERS])
    result = run(engine, sec, model=FakeModel(replies={}), cfg=Config(graph_max_linked=4),
                 directory=_directory_with_peers())
    assert result.status == "done" and result.sector_fallback
    assert {l.symbol for l in stored_links(engine)} == {"PEER1", "PEER2", "PEER3", "PEER4"}  # up to the cap


def test_model_outage_with_industry_peers_is_still_an_error(engine):
    # The model fails everywhere but SEC's industry list works: peers are saved and shown, yet the
    # run is an error so the next search retries instead of caching a peers-only graph for a week.
    sec = Sec(industry=[cik for cik, _, _ in EXTRA_PEERS])
    result = run(engine, sec, model=FakeModel(fail=True), directory=_directory_with_peers())
    assert result.status == "error" and "no filing links saved" in result.error
    assert link_run(engine).status == "error"
    assert {l.source for l in stored_links(engine)} == {"sector"}
