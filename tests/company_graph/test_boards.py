"""Tests for company_graph.boards. No network: SEC goes to an httpx.MockTransport and the database
is in-memory SQLite behind the AsyncSession shim from test_links."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import Session

from backend.models.graph_board_run import GraphBoardRun
from backend.models.graph_board_seat import GraphBoardSeat
from backend.models.graph_link_run import GraphLinkRun
from company_graph import boards
from company_graph.boards import (
    ReportingOwner,
    build_board,
    display_name,
    member_id,
    parse_ownership,
    pick_directors,
    raw_xml_document,
    read_board,
)
from company_graph.config import Config
from company_graph.schemas import BoardResponse, board_fixture_tickers, fixture_tickers, load_board_fixture
from company_graph.sec import SUBMISSIONS_URL, Filing, RateLimiter, SecClient, filing_url
from tests.company_graph.test_links import DIRECTORY, AsyncSessionShim, engine  # noqa: F401 - engine is a fixture

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
TSLA_CIK = 1318605
SUBMISSIONS = SUBMISSIONS_URL.format(cik=TSLA_CIK)


def owner_xml(cik, name, director="1", officer="0", title=None):
    title_xml = f"<officerTitle>{title}</officerTitle>" if title else ""
    return (f"<reportingOwner><reportingOwnerId><rptOwnerCik>{cik:010d}</rptOwnerCik>"
            f"<rptOwnerName>{name}</rptOwnerName></reportingOwnerId>"
            f"<reportingOwnerAddress><rptOwnerCity>AUSTIN</rptOwnerCity></reportingOwnerAddress>"
            f"<reportingOwnerRelationship><isDirector>{director}</isDirector><isOfficer>{officer}</isOfficer>"
            f"<isTenPercentOwner>0</isTenPercentOwner><isOther>0</isOther>{title_xml}"
            f"</reportingOwnerRelationship></reportingOwner>")


def ownership_xml(*owners, issuer_cik=TSLA_CIK, form="4"):
    return (f'<?xml version="1.0"?>\n<ownershipDocument><schemaVersion>X0508</schemaVersion>'
            f"<documentType>{form}</documentType><periodOfReport>2026-08-12</periodOfReport>"
            f"<issuer><issuerCik>{issuer_cik:010d}</issuerCik><issuerName>Tesla, Inc.</issuerName>"
            f"<issuerTradingSymbol>TSLA</issuerTradingSymbol></issuer>{''.join(owners)}"
            f"<nonDerivativeTable></nonDerivativeTable></ownershipDocument>")


# --- parsing --------------------------------------------------------------------------

def test_parse_reads_directors_officers_and_the_issuer():
    doc = parse_ownership(ownership_xml(
        owner_xml(1001, "QUILLFEATHER MARIGOLD A"),
        owner_xml(1002, "VEXLEY THADDEUS", director="true", officer="true", title="Chief Executive Officer"),
        owner_xml(1003, "PIMMSWICK PERCIVAL", director="0", officer="1", title="CFO"),
    ))
    assert doc.issuer_cik == TSLA_CIK
    assert doc.owners == [
        ReportingOwner(1001, "QUILLFEATHER MARIGOLD A", is_director=True),
        ReportingOwner(1002, "VEXLEY THADDEUS", is_director=True, is_officer=True, officer_title="Chief Executive Officer"),
        ReportingOwner(1003, "PIMMSWICK PERCIVAL", is_director=False, is_officer=True, officer_title="CFO"),
    ]


@pytest.mark.parametrize("flag,expected", [("1", True), ("true", True), ("TRUE", True), (" 1 ", True),
                                           ("0", False), ("false", False), ("", False)])
def test_parse_accepts_flags_as_numbers_or_words(flag, expected):
    assert parse_ownership(ownership_xml(owner_xml(1001, "A B", director=flag))).owners[0].is_director is expected


def test_parse_tolerates_missing_fields_and_namespaces():
    doc = parse_ownership(
        '<ownershipDocument xmlns="urn:example"><reportingOwner><reportingOwnerId>'
        "<rptOwnerName>  NOBODY   KNOWN </rptOwnerName></reportingOwnerId></reportingOwner>"
        "<reportingOwner/></ownershipDocument>")
    assert doc.issuer_cik is None
    assert doc.owners == [ReportingOwner(None, "NOBODY KNOWN"), ReportingOwner(None, "")]
    assert parse_ownership("<ownershipDocument/>").owners == []


def test_parse_rejects_text_that_is_not_xml():
    with pytest.raises(ValueError, match="ownership XML"):
        parse_ownership("<html><body>Request Rate Threshold Exceeded<br></body></html>")


# --- names and ids ----------------------------------------------------------------------

@pytest.mark.parametrize("raw,shown", [
    ("QUILLFEATHER MARIGOLD A", "Marigold A Quillfeather"),
    ("Vexley Thaddeus", "Thaddeus Vexley"),
    ("MCSNODGROVE LEOPOLD R III", "Leopold R McSnodgrove III"),
    ("O'FENN MARY-ANNE", "Mary-Anne O'Fenn"),
    ("Tugwillow, Montague B., Jr.", "Montague B. Tugwillow Jr."),
    ("BRAMBLE FAMILY TRUST", "Bramble Family Trust"),   # not a person: not reordered
    ("CROWMARSH", "Crowmarsh"),
    ("  ", ""),
])
def test_display_name(raw, shown):
    assert display_name(raw) == shown


def test_member_id_is_the_ten_digit_cik():
    assert member_id(1234567) == "cik-0001234567"


@pytest.mark.parametrize("listed,raw", [
    ("xslF345X05/wk-form4_1.xml", "wk-form4_1.xml"),
    ("xslF345X02/primary_doc.xml", "primary_doc.xml"),
    ("form4.xml", "form4.xml"),
    ("form4.txt", None),
    ("", None),
])
def test_raw_xml_document_drops_the_stylesheet_folder(listed, raw):
    assert raw_xml_document(listed) == raw


# --- dedupe -----------------------------------------------------------------------------

def filing(accession, filed, document="xslF345X05/form4.xml", form="4"):
    return Filing(accession, form, date.fromisoformat(filed), None, document, filing_url(TSLA_CIK, accession, document))


def test_pick_directors_keeps_the_newest_filing_per_person():
    old, new = filing("0001-26-000001", "2026-02-01"), filing("0001-26-000002", "2026-08-01")
    directors, others = pick_directors([
        (old, ReportingOwner(1001, "QUILLFEATHER MARIGOLD", is_director=True)),
        (new, ReportingOwner(1001, "QUILLFEATHER MARIGOLD A", is_director=True, is_officer=True, officer_title="Chair")),
        (new, ReportingOwner(1002, "VEXLEY THADDEUS", is_director=True)),
        (old, ReportingOwner(1003, "PIMMSWICK PERCIVAL", is_officer=True, officer_title="CFO")),   # never a director
        (old, ReportingOwner(1004, "UNDERHOLLOW ZENOBIA", is_director=True)),
        (new, ReportingOwner(1004, "UNDERHOLLOW ZENOBIA")),                                        # has left the board
        (new, ReportingOwner(None, "NO CIK", is_director=True)),
    ])
    assert [(d.person_cik, d.name, d.role, d.is_officer, d.filed_at) for d in directors] == [
        (1001, "Marigold A Quillfeather", "Chair", True, date(2026, 8, 1)),
        (1002, "Thaddeus Vexley", "Director", False, date(2026, 8, 1)),
    ]
    assert directors[0].raw_name == "QUILLFEATHER MARIGOLD A" and directors[0].evidence_url == new.url
    assert others == {1003, 1004}


def test_pick_directors_breaks_same_day_ties_by_accession_number():
    a, b = filing("0001-26-000001", "2026-08-01"), filing("0001-26-000002", "2026-08-01")
    for entries in ([(a, ReportingOwner(1, "A B", is_director=True)), (b, ReportingOwner(1, "A B"))],
                    [(b, ReportingOwner(1, "A B")), (a, ReportingOwner(1, "A B", is_director=True))]):
        assert pick_directors(entries) == ([], {1})


# --- the run ----------------------------------------------------------------------------

# (accession, filed, form, primary document, XML)
FILINGS = [
    ("0001-26-000009", "2026-09-20", "4", "xslF345X05/a.xml",
     ownership_xml(owner_xml(1002, "VEXLEY THADDEUS", officer="1", title="Chief Executive Officer"))),
    ("0001-26-000008", "2026-08-15", "4", "xslF345X05/b.xml",
     ownership_xml(owner_xml(1001, "QUILLFEATHER MARIGOLD A"), owner_xml(1005, "QUILLFEATHER FAMILY TRUST", director="0"))),
    ("0001-26-000007", "2026-07-01", "4", "xslF345X05/c.xml",
     ownership_xml(owner_xml(1003, "PIMMSWICK PERCIVAL", director="0", officer="1", title="CFO"))),
    ("0001-26-000006", "2026-06-01", "3", "xslF345X02/d.xml", ownership_xml(owner_xml(1004, "UNDERHOLLOW ZENOBIA"), form="3")),
    ("0001-26-000005", "2026-05-01", "4", "xslF345X05/e.xml",   # Tesla reporting its own stake in another company
     ownership_xml(owner_xml(TSLA_CIK, "Tesla, Inc.", director="1"), issuer_cik=4242)),
    ("0001-26-000004", "2026-04-01", "4", "xslF345X05/f.xml", ownership_xml(owner_xml(1001, "QUILLFEATHER MARIGOLD"))),
    ("0001-26-000003", "2026-03-01", "10-K", "tsla-10k.htm", "<html/>"),
    ("0001-25-000001", "2025-03-01", "4", "xslF345X05/old.xml", ownership_xml(owner_xml(1006, "TOO OLD"))),
]


class Sec:
    """A MockTransport-backed SecClient factory that records every request."""

    def __init__(self, filings=FILINGS, status=None, broken=(), slow=()):
        self.filings, self.status, self.broken, self.slow = filings, status, set(broken), set(slow)
        self.urls: list[str] = []
        self.docs = {filing_url(TSLA_CIK, acc, boards.raw_xml_document(doc) or doc): xml
                     for acc, _, _, doc, xml in filings}

    async def handler(self, request):
        url = str(request.url)
        self.urls.append(url)
        if self.status:
            return httpx.Response(self.status)
        if url == SUBMISSIONS:
            return httpx.Response(200, json={"cik": "0001318605", "name": "Tesla, Inc.", "filings": {"recent": {
                "accessionNumber": [f[0] for f in self.filings], "filingDate": [f[1] for f in self.filings],
                "reportDate": ["" for _ in self.filings], "form": [f[2] for f in self.filings],
                "primaryDocument": [f[3] for f in self.filings]}}})
        if any(url.endswith(name) for name in self.slow):
            await asyncio.sleep(5)
        if any(url.endswith(name) for name in self.broken):
            return httpx.Response(200, text="<html>not xml<br></html>")
        if url in self.docs:
            return httpx.Response(200, text=self.docs[url])
        return httpx.Response(404)

    def client(self):
        return SecClient("test@example.com", cache_dir=None, limiter=RateLimiter(rate=10_000), max_retries=0,
                         transport=httpx.MockTransport(self.handler))

    def archive_urls(self):
        return [u for u in self.urls if "/Archives/" in u]


def run(engine, sec, *, cfg=None, now=NOW, budget=60.0, symbol="TSLA"):
    async def go():
        with Session(engine, expire_on_commit=False) as s:
            async with sec.client() as client:
                return await build_board(symbol, session=AsyncSessionShim(s), sec=client, cfg=cfg or Config(),
                                         directory=DIRECTORY, time_budget_s=budget, now=now)
    return asyncio.run(go())


def seats(engine, symbol="TSLA"):
    with Session(engine) as s:
        return {r.person_cik: r for r in s.query(GraphBoardSeat).filter_by(company_symbol=symbol)}


def run_row(engine, symbol="TSLA"):
    with Session(engine) as s:
        return s.get(GraphBoardRun, symbol)


def test_build_stores_one_seat_per_director(engine):  # noqa: F811
    sec = Sec()
    result = run(engine, sec)
    assert (result.status, result.error, result.errors, result.skipped) == ("done", None, [], False)
    assert result.members == ["Marigold A Quillfeather", "Thaddeus Vexley", "Zenobia Underhollow"]
    assert result.filings_read == 6

    stored = seats(engine)
    assert set(stored) == {1001, 1002, 1004}  # not the CFO, the trust, Tesla itself or the 2025 filer
    chair, ceo = stored[1001], stored[1002]
    assert (chair.name, chair.raw_name, chair.role, chair.is_officer) == (
        "Marigold A Quillfeather", "QUILLFEATHER MARIGOLD A", "Director", False)
    assert chair.filed_at == date(2026, 8, 15)  # the newer of her two filings
    assert chair.evidence_url == filing_url(TSLA_CIK, "0001-26-000008", "xslF345X05/b.xml")
    assert (ceo.role, ceo.is_officer) == ("Chief Executive Officer", True)

    # The XML itself is fetched, never the rendered view, and only Forms 3 / 4 inside the lookback.
    assert sorted(u.rsplit("/", 1)[-1] for u in sec.archive_urls()) == ["a.xml", "b.xml", "c.xml", "d.xml", "e.xml", "f.xml"]
    assert not any("xslF345" in u for u in sec.urls)
    assert run_row(engine).status == "done"
    with Session(engine) as s:
        assert s.get(GraphLinkRun, "TSLA") is None  # link runs are untouched


def test_build_reads_only_the_newest_filings_up_to_the_cap(engine):  # noqa: F811
    sec = Sec()
    result = run(engine, sec, cfg=Config(graph_board_max_filings=2))
    assert sorted(u.rsplit("/", 1)[-1] for u in sec.archive_urls()) == ["a.xml", "b.xml"]
    assert result.members == ["Marigold A Quillfeather", "Thaddeus Vexley"]


def test_fresh_run_is_skipped_and_a_stale_one_is_rebuilt(engine):  # noqa: F811
    run(engine, Sec())
    again = Sec()
    result = run(engine, again, now=NOW + timedelta(days=1))
    assert (result.status, result.skipped, again.urls) == ("done", True, [])

    later = Sec()
    result = run(engine, later, now=NOW + timedelta(days=8))
    assert result.skipped is False and later.urls[0] == SUBMISSIONS
    assert set(seats(engine)) == {1001, 1002, 1004}


def test_run_in_progress_elsewhere_is_skipped(engine):  # noqa: F811
    with Session(engine) as s:
        s.add(GraphBoardRun(symbol="TSLA", status="running", fetched_at=NOW - timedelta(minutes=1)))
        s.commit()
    sec = Sec()
    result = run(engine, sec)
    assert (result.status, result.skipped, sec.urls) == ("running", True, [])


def test_rebuild_drops_departed_directors_and_keeps_ones_it_did_not_read(engine):  # noqa: F811
    run(engine, Sec())
    newer = [
        # Underhollow's newest filing no longer says director: she has left.
        ("0001-26-000020", "2026-10-15", "4", "xslF345X05/g.xml", ownership_xml(owner_xml(1004, "UNDERHOLLOW ZENOBIA", director="0"))),
        ("0001-26-000021", "2026-10-16", "3", "xslF345X05/h.xml", ownership_xml(owner_xml(1007, "FENNIMORE BARNABY"), form="3")),
    ]
    result = run(engine, Sec(filings=newer), now=NOW + timedelta(days=8))
    assert result.members == ["Barnaby Fennimore"]
    assert set(seats(engine)) == {1001, 1002, 1007}  # 1001 and 1002 were not in this run's filings: kept

    # Much later, with no filings at all: seats older than the lookback are dropped.
    result = run(engine, Sec(filings=[]), now=NOW + timedelta(days=600))
    assert result.status == "done" and seats(engine) == {}


def test_company_without_ownership_filings_is_done_and_empty(engine):  # noqa: F811
    sec = Sec(filings=[("0001-26-000003", "2026-03-01", "20-F", "annual.htm", "<html/>")])
    result = run(engine, sec)
    assert (result.status, result.members, result.filings_read) == ("done", [], 0)
    assert sec.archive_urls() == [] and seats(engine) == {}
    assert run_row(engine).status == "done"


def test_one_bad_filing_does_not_end_the_run(engine):  # noqa: F811
    result = run(engine, Sec(broken={"/b.xml"}))
    assert result.status == "done" and len(result.errors) == 1 and "b.xml" in result.errors[0]
    assert set(seats(engine)) == {1001, 1002, 1004}  # Quillfeather still found, from her older filing
    assert seats(engine)[1001].filed_at == date(2026, 4, 1)


def test_sec_outage_is_an_error_and_keeps_stored_seats(engine):  # noqa: F811
    run(engine, Sec())
    result = run(engine, Sec(status=503), now=NOW + timedelta(days=8))
    assert result.status == "error" and "SecRequestError" in result.error
    assert run_row(engine).status == "error" and run_row(engine).error
    assert set(seats(engine)) == {1001, 1002, 1004}


def test_every_filing_failing_is_an_error_not_an_empty_board(engine):  # noqa: F811
    result = run(engine, Sec(broken={".xml"}))
    assert result.status == "error" and "no ownership filing read" in result.error
    assert run_row(engine).status == "error"


def test_timeout_keeps_what_was_read(engine):  # noqa: F811
    result = run(engine, Sec(slow={"/a.xml"}), budget=0.5)
    assert result.status == "done" and result.timed_out is True
    assert set(seats(engine)) == {1001, 1004}  # the CEO's filing was the slow one


def test_unknown_company_is_an_error(engine):  # noqa: F811
    sec = Sec()
    result = run(engine, sec, symbol="ZZZZ")
    assert result.status == "error" and "unknown company" in result.error and sec.urls == []


def test_fake_mode_reads_and_writes_nothing(engine):  # noqa: F811
    sec = Sec()
    result = run(engine, sec, cfg=Config(graph_fake=1))
    assert (result.status, result.skipped, sec.urls) == ("done", True, [])
    assert run_row(engine) is None


def test_read_board_is_sorted_by_name(engine):  # noqa: F811
    run(engine, Sec())

    async def go():
        with Session(engine) as s:
            return [r.name for r in await read_board(AsyncSessionShim(s), "tsla")]

    assert asyncio.run(go()) == ["Marigold A Quillfeather", "Thaddeus Vexley", "Zenobia Underhollow"]


# --- fixtures ---------------------------------------------------------------------------

def test_board_fixtures_cover_the_graph_fixtures_and_are_kept_apart():
    assert set(fixture_tickers()) <= set(board_fixture_tickers())
    assert fixture_tickers() == ["AAPL", "NVDA", "TSLA"]  # board files must not show up as graph fixtures
    for ticker in board_fixture_tickers():
        board = load_board_fixture(ticker)
        assert board.company.symbol == ticker and board.status == "done"
        ids = [m.id for m in board.members]
        assert len(ids) == len(set(ids))
        # Fictional people: ids outside the range SEC has issued, and no sec.gov evidence.
        assert all(m.id.startswith("cik-99900000") and m.evidence_url.startswith("https://example.com/fixture/")
                   for m in board.members)
        assert all(date.fromisoformat(m.filed_at) for m in board.members)
    assert load_board_fixture("ZZZZ") is None


def test_board_fixtures_have_an_interlock():
    boards_by_person: dict[str, set[str]] = {}
    for ticker in board_fixture_tickers():
        for m in load_board_fixture(ticker).members:
            boards_by_person.setdefault(m.id, set()).add(ticker)
    shared = {pid: where for pid, where in boards_by_person.items() if len(where) > 1}
    assert {"TSLA", "NVDA"} in shared.values()
    names = {m.id: m.name for t in board_fixture_tickers() for m in load_board_fixture(t).members}
    for ticker in board_fixture_tickers():  # one id, one name, on every board
        assert all(names[m.id] == m.name for m in load_board_fixture(ticker).members)


def test_board_shape_rejects_unknown_fields_and_values():
    good = load_board_fixture("TSLA").model_dump()
    BoardResponse.model_validate(good)
    with pytest.raises(ValueError):
        BoardResponse.model_validate({**good, "extra": 1})
    with pytest.raises(ValueError):
        BoardResponse.model_validate({**good, "status": "pending"})
