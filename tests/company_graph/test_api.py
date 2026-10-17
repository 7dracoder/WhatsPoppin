"""Tests for company_graph.api (F9). No network and no Postgres: the router is mounted on a throwaway
FastAPI app, the database is a SQLite file behind a small AsyncSession shim, build_links is a fake,
and the company directory is a fixed list."""

import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.models.entity import Entity
from backend.models.entity_relationship import EntityRelationship
from backend.models.graph_company_profile import GraphCompanyProfile
from backend.models.graph_event import GraphEvent
from backend.models.graph_highlight import GraphHighlight
from backend.models.graph_link_run import GraphLinkRun
from company_graph import api
from company_graph.companies import CompanyDirectory
from company_graph.config import Config
from company_graph.db import create_tables
from company_graph.links import LinkRunResult
from company_graph.schemas import GraphResponse, load_fixture

DIRECTORY = CompanyDirectory.from_sec_json({str(i): {"cik_str": cik, "ticker": t, "title": n} for i, (cik, t, n) in enumerate([
    (1318605, "TSLA", "Tesla, Inc."),
    (1045810, "NVDA", "NVIDIA CORP"),
    (37996, "F", "Ford Motor Co"),
    (1467858, "GM", "General Motors Co"),
    (915913, "ALB", "ALBEMARLE CORP"),
    (320193, "AAPL", "Apple Inc."),
    (1, "TEAM", "Atlassian Corp"),
])})

URL = "https://www.sec.gov/Archives/edgar/data/1318605/000162828026003952/tsla-20251231.htm"


class AsyncSessionShim:
    """The AsyncSession calls links.py and api.py make, run on a sync SQLite Session (aiosqlite is not installed)."""

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


class SqliteDb:
    """Stands in for api.GraphDb."""

    def __init__(self, engine):
        self.engine = engine
        self.run_sessions = 0

    @asynccontextmanager
    async def session(self):
        with Session(self.engine, expire_on_commit=False) as s:
            yield AsyncSessionShim(s)

    @asynccontextmanager
    async def run_session(self):
        self.run_sessions += 1
        with Session(self.engine, expire_on_commit=False) as s:
            yield AsyncSessionShim(s)


def _now():
    return datetime.now(timezone.utc)


def seed_links(s: Session, symbol="TSLA"):
    for sym, name in [(symbol, "Tesla, Inc."), ("NVDA", "NVIDIA CORP"), ("F", "Ford Motor Co"), ("ALB", "ALBEMARLE CORP")]:
        if s.get(Entity, sym) is None:
            s.add(Entity(symbol=sym, name=name, type="company"))
    s.flush()
    if s.query(EntityRelationship).count():
        return  # already seeded (a rebuild over stale links)
    s.add_all([
        EntityRelationship(entity_symbol=symbol, related_entity_symbol="NVDA", relationship_type="partner",
                           confidence=0.9, source="filing", summary="Tesla has an agreement with NVIDIA.", evidence_url=URL),
        EntityRelationship(entity_symbol=symbol, related_entity_symbol="F", relationship_type="competitor",
                           confidence=0.9, source="filing", summary="Tesla names Ford as a competitor.", evidence_url=URL),
        # Stored from Albemarle's side (Tesla is its customer): read_links flips it to "ALB supplies TSLA".
        EntityRelationship(entity_symbol="ALB", related_entity_symbol=symbol, relationship_type="customer",
                           confidence=0.9, source="filing", summary="Albemarle names Tesla as a customer.", evidence_url=URL),
        # A second type for NVDA: the node keeps the preferred one (supplier before partner).
        EntityRelationship(entity_symbol=symbol, related_entity_symbol="NVDA", relationship_type="supplier",
                           confidence=0.9, source="filing", summary="NVIDIA supplies Tesla with GPUs.", evidence_url=URL),
    ])


def set_run(s: Session, symbol: str, status: str, at: datetime, error=None):
    row = s.get(GraphLinkRun, symbol) or GraphLinkRun(symbol=symbol)
    row.status, row.fetched_at, row.error = status, at, error
    s.add(row)


class FakeBuild:
    """Stands in for links.build_links: marks the run running, waits for `gate`, saves links, ends done or error."""

    def __init__(self, outcome="done", wait=False):
        self.outcome = outcome
        self.calls: list[str] = []
        self.gate = threading.Event()
        self.finished = threading.Event()
        if not wait:
            self.gate.set()

    async def __call__(self, symbol, *, session, cfg, directory):
        self.calls.append(symbol)
        set_run(session.s, symbol, "running", _now())
        await session.commit()
        await asyncio.to_thread(self.gate.wait, 5)
        if self.outcome == "done":
            seed_links(session.s, symbol)
        set_run(session.s, symbol, self.outcome, _now(), "SecError: down" if self.outcome == "error" else None)
        await session.commit()
        self.finished.set()
        return LinkRunResult(symbol=symbol, status=self.outcome)


class FakeHighlights:
    """Stands in for highlights.build_highlights: records calls, waits for `gate`, optionally saves
    one highlight per call (for ALB, on a TSLA contract event) or raises."""

    def __init__(self, wait=False, save=False, error=None):
        self.calls: list[str] = []
        self.save = save
        self.error = error
        self.gate = threading.Event()
        self.finished = threading.Event()
        if not wait:
            self.gate.set()

    async def __call__(self, symbol, *, session, cfg, directory):
        self.calls.append(symbol)
        await asyncio.to_thread(self.gate.wait, 5)
        try:
            if self.error:
                raise self.error
            if self.save:
                now = _now()
                ev = GraphEvent(entity_symbol=symbol, source="news", event_type="contract", title="Cell order",
                                url=f"https://news.example/{len(self.calls)}", occurred_at=now - timedelta(hours=2))
                session.add(ev)
                await session.flush()
                session.add(GraphHighlight(event_id=ev.id, source_symbol=symbol, target_symbol="ALB",
                                           direction="may_benefit", reason="Tesla signed a cell order.",
                                           source_url=ev.url, event_time=ev.occurred_at))
                await session.commit()
        finally:
            self.finished.set()
        return None


def wait_for_highlights_done():
    for _ in range(100):  # the task's done-callback runs on the app's loop
        if not api._HIGHLIGHT_RUNS:
            return
        threading.Event().wait(0.02)


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'graph.db'}", connect_args={"check_same_thread": False})
    event.listen(eng, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    with eng.begin() as conn:
        create_tables(conn)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_runs():
    for registry in (api._RUNS, api._HIGHLIGHT_RUNS, api._HIGHLIGHTS_DONE):
        registry.clear()
    yield
    for registry in (api._RUNS, api._HIGHLIGHT_RUNS, api._HIGHLIGHTS_DONE):
        registry.clear()


def make_app(*, cfg=None, db=None, build=None, directory=DIRECTORY, highlights=None):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_config] = lambda: cfg or Config()
    if db is not None:
        app.dependency_overrides[api.get_db] = lambda: db
    app.dependency_overrides[api.get_company_directory] = lambda: directory
    app.dependency_overrides[api.get_link_builder] = lambda: build or FakeBuild()
    app.dependency_overrides[api.get_highlight_builder] = lambda: highlights or FakeHighlights()
    return app


# --- the polling flow ------------------------------------------------------------------

def test_first_request_runs_then_done_with_links(engine):
    build = FakeBuild(wait=True)
    db = SqliteDb(engine)
    with TestClient(make_app(db=db, build=build)) as client:
        first = client.get("/graph/TSLA")
        assert first.status_code == 200
        body = first.json()
        assert body["status"] == "running"
        assert body["company"] == {"symbol": "TSLA", "name": "Tesla, Inc.", "industry": None}  # no profile saved
        assert body["nodes"] == [] and body["links"] == []

        # A poll while the run is going: still running, and no second run.
        assert client.get("/graph/TSLA").json()["status"] == "running"
        assert client.get("/graph/tsla").json()["status"] == "running"
        assert build.calls == ["TSLA"]

        build.gate.set()
        assert build.finished.wait(5)
        for _ in range(50):  # the task's done-callback runs on the app's loop
            if not api._RUNS:
                break
            threading.Event().wait(0.02)

        # Links are done: the first such poll starts the highlight run (its own session) and says running.
        assert client.get("/graph/TSLA").json()["status"] == "running"
        wait_for_highlights_done()
        done = client.get("/graph/TSLA")
    body = GraphResponse.model_validate(done.json())
    assert body.status == "done"
    assert build.calls == ["TSLA"] and db.run_sessions == 2
    assert [n.symbol for n in body.nodes] == ["ALB", "NVDA", "F"]
    assert {n.symbol: n.type for n in body.nodes} == {"ALB": "supplier", "NVDA": "supplier", "F": "competitor"}
    assert all(n.symbol != "TSLA" for n in body.nodes)
    assert {(l.source, l.target, l.type) for l in body.links} == {
        ("TSLA", "ALB", "supplier"), ("TSLA", "NVDA", "supplier"), ("TSLA", "NVDA", "partner"), ("TSLA", "F", "competitor")}
    assert all(l.evidence_url == URL for l in body.links)
    assert body.highlights == []


def test_fresh_links_are_done_without_a_run(engine):
    with Session(engine) as s:
        seed_links(s)
        set_run(s, "TSLA", "done", _now() - timedelta(days=1))
        s.commit()
    build = FakeBuild()
    highlights = FakeHighlights()
    with TestClient(make_app(db=SqliteDb(engine), build=build, highlights=highlights)) as client:
        first = client.get("/graph/TSLA").json()  # links are fresh; highlights have not run yet
        wait_for_highlights_done()
        body = client.get("/graph/TSLA").json()
    assert first["status"] == "running" and len(first["links"]) == 4
    assert body["status"] == "done" and len(body["links"]) == 4
    assert build.calls == [] and highlights.calls == ["TSLA"]


def test_industry_comes_from_the_company_profiles(engine):
    with Session(engine) as s:
        seed_links(s)
        set_run(s, "TSLA", "done", _now() - timedelta(days=1))
        s.add_all([
            GraphCompanyProfile(symbol="TSLA", cik=1318605, sic_code="3711",
                                sic_description="Motor Vehicles & Passenger Car Bodies"),
            GraphCompanyProfile(symbol="F", cik=37996, sic_code="3711"),  # code only: borrows TSLA's description
            GraphCompanyProfile(symbol="ALB", cik=915913, sic_code="2821"),  # a code nobody here describes
        ])
        s.commit()
    with TestClient(make_app(db=SqliteDb(engine), build=FakeBuild(), highlights=FakeHighlights())) as client:
        body = client.get("/graph/TSLA").json()
        wait_for_highlights_done()
    industry = {n["symbol"]: n["industry"] for n in body["nodes"]}
    assert body["company"]["industry"] == "Motor Vehicles & Passenger Car Bodies"
    assert industry == {"F": "Motor Vehicles & Passenger Car Bodies", "ALB": None, "NVDA": None}


def test_stale_links_are_returned_while_a_new_run_starts(engine):
    with Session(engine) as s:
        seed_links(s)
        set_run(s, "TSLA", "done", _now() - timedelta(days=30))
        s.commit()
    build = FakeBuild(wait=True)
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        body = client.get("/graph/TSLA").json()
        build.gate.set()
        assert build.finished.wait(5)
    assert body["status"] == "running" and len(body["links"]) == 4
    assert build.calls == ["TSLA"]


def test_run_in_progress_elsewhere_is_not_started_again(engine):
    with Session(engine) as s:
        set_run(s, "TSLA", "running", _now() - timedelta(minutes=1))
        s.commit()
    build = FakeBuild()
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        assert client.get("/graph/TSLA").json()["status"] == "running"
    assert build.calls == []


def test_crashed_running_row_is_restarted(engine):
    with Session(engine) as s:
        set_run(s, "TSLA", "running", _now() - timedelta(minutes=30))
        s.commit()
    build = FakeBuild()
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        assert client.get("/graph/TSLA").json()["status"] == "running"
        assert build.finished.wait(5)
    assert build.calls == ["TSLA"]


def test_recent_error_is_reported_with_existing_links(engine):
    with Session(engine) as s:
        seed_links(s)
        set_run(s, "TSLA", "error", _now() - timedelta(seconds=10), "SecError: down")
        s.commit()
    build = FakeBuild()
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        body = client.get("/graph/TSLA").json()
    assert body["status"] == "error" and len(body["links"]) == 4
    assert build.calls == []


def test_run_that_errors_reports_error_then_retries_later(engine):
    build = FakeBuild(outcome="error")
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        assert client.get("/graph/TSLA").json()["status"] == "running"
        assert build.finished.wait(5)
        for _ in range(50):
            if not api._RUNS:
                break
            threading.Event().wait(0.02)
        assert client.get("/graph/TSLA").json()["status"] == "error"
        with Session(engine) as s:
            set_run(s, "TSLA", "error", _now() - timedelta(seconds=api.ERROR_RETRY_S + 60), "SecError: down")
            s.commit()
        build.finished.clear()
        assert client.get("/graph/TSLA").json()["status"] == "running"
        assert build.finished.wait(5)
    assert build.calls == ["TSLA", "TSLA"]


def test_unknown_ticker_is_404(engine):
    build = FakeBuild()
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        resp = client.get("/graph/ZZZZ")
    assert resp.status_code == 404
    assert "ZZZZ" in resp.json()["detail"]
    assert build.calls == []


def test_missing_database_url_is_503():
    with TestClient(make_app(cfg=Config(database_url=""))) as client:
        resp = client.get("/graph/TSLA")
    assert resp.status_code == 503
    assert "DATABASE_URL" in resp.json()["detail"]


# --- highlights ----------------------------------------------------------------------

def test_highlights_come_from_the_table_for_shown_nodes_in_the_window(engine):
    now = _now()
    with Session(engine) as s:
        seed_links(s)
        set_run(s, "TSLA", "done", now - timedelta(hours=1))
        ev = GraphEvent(entity_symbol="TSLA", source="news", event_type="contract", title="Cell order",
                        url="https://news.example/a", occurred_at=now - timedelta(days=1))
        s.add(ev)
        s.flush()

        def hl(target, days, url="https://news.example/a", direction="may_benefit"):
            return GraphHighlight(event_id=ev.id, source_symbol="TSLA", target_symbol=target, direction=direction,
                                  reason="Tesla signed a contract.", source_url=url,
                                  event_time=now - timedelta(days=days), price_change_pct=1.5)

        s.add_all([hl("ALB", 1), hl("F", 2, url="https://news.example/b", direction="may_face_pressure"),
                   hl("NVDA", 30),     # outside the 7-day window
                   hl("AAPL", 1)])     # not a node of this graph
        s.commit()
    with TestClient(make_app(db=SqliteDb(engine))) as client:
        body = GraphResponse.model_validate(client.get("/graph/TSLA").json())
    assert [(h.target, h.direction) for h in body.highlights] == [("ALB", "may_benefit"), ("F", "may_face_pressure")]
    assert body.highlights[0].event_type == "contract"
    assert body.highlights[0].price_change_pct == 1.5
    assert body.highlights[0].event_time.endswith("Z")


def test_f8_hook_runs_only_when_done(engine, monkeypatch):
    calls = []

    async def hook(session, company, nodes, cfg, **kwargs):
        calls.append((company.symbol, [n.symbol for n in nodes]))
        return False

    monkeypatch.setattr(api, "refresh_highlights", hook)
    with Session(engine) as s:
        seed_links(s)
        set_run(s, "TSLA", "running", _now())
        s.commit()
    with TestClient(make_app(db=SqliteDb(engine))) as client:
        client.get("/graph/TSLA")
        assert calls == []
        with Session(engine) as s:
            set_run(s, "TSLA", "done", _now())
            s.commit()
        client.get("/graph/TSLA")
    assert calls == [("TSLA", ["ALB", "NVDA", "F"])]


# --- search --------------------------------------------------------------------------

def test_search_uses_the_directory():
    with TestClient(make_app()) as client:
        hits = client.get("/companies/search", params={"q": "te"}).json()
        # Ticker prefix first, then name prefix.
        assert hits == [{"symbol": "TEAM", "name": "Atlassian Corp"}, {"symbol": "TSLA", "name": "Tesla, Inc."}]
        assert client.get("/companies/search", params={"q": ""}).json() == []
        assert client.get("/companies/search").json() == []
        assert client.get("/companies/search", params={"q": "nvidia"}).json() == [{"symbol": "NVDA", "name": "NVIDIA CORP"}]


def test_search_caps_at_ten():
    many = CompanyDirectory.from_sec_json({str(i): {"cik_str": i, "ticker": f"A{i}", "title": f"Alpha {i}"} for i in range(30)})
    with TestClient(make_app(directory=many)) as client:
        assert len(client.get("/companies/search", params={"q": "a"}).json()) == 10


# --- fake mode -----------------------------------------------------------------------

def fake_app():
    """Fake mode with no overrides for the database or the directory: touching either would fail."""
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_config] = lambda: Config(graph_fake=1)

    def boom():
        raise AssertionError("fake mode must not build links")

    app.dependency_overrides[api.get_link_builder] = lambda: boom
    return app


def test_fake_mode_serves_fixtures_without_database_or_network(monkeypatch):
    def no_directory():
        raise AssertionError("fake mode must not load the company directory")

    monkeypatch.setattr(api, "get_directory", no_directory)
    with TestClient(fake_app()) as client:
        tsla = client.get("/graph/TSLA")
        assert tsla.status_code == 200
        assert tsla.json() == load_fixture("TSLA").model_dump()
        assert client.get("/graph/tsla").json()["company"]["symbol"] == "TSLA"

        unknown = client.get("/graph/ZZZZ").json()
        assert unknown == {"company": {"symbol": "ZZZZ", "name": "ZZZZ", "industry": None}, "status": "done",
                           "links_status": None, "nodes": [], "links": [], "highlights": []}

        hits = client.get("/companies/search", params={"q": "te"}).json()
        assert {"symbol": "TSLA", "name": "Tesla, Inc."} in hits
        assert len(hits) <= 10
        assert client.get("/companies/search", params={"q": "nvda"}).json()[0]["symbol"] == "NVDA"
        assert client.get("/companies/search", params={"q": " "}).json() == []
    assert api._DBS == {}


def test_fake_search_matches_symbol_prefix_or_name():
    hits = api.fake_search("pana")
    assert [h.symbol for h in hits] == ["PCRFY"]
    assert api.fake_search("") == []


# --- pure helpers --------------------------------------------------------------------

def test_run_status():
    cfg = Config()
    now = _now()

    def run(status, age_s):
        return GraphLinkRun(symbol="X", status=status, fetched_at=now - timedelta(seconds=age_s))

    assert api.run_status(None, now, cfg, False) == "start"
    assert api.run_status(None, now, cfg, True) == "running"
    assert api.run_status(run("done", 60), now, cfg, False) == "done"
    assert api.run_status(run("done", cfg.link_ttl_s + 1), now, cfg, False) == "start"
    assert api.run_status(run("running", 60), now, cfg, False) == "running"
    assert api.run_status(run("running", 3600), now, cfg, False) == "start"
    assert api.run_status(run("error", 10), now, cfg, False) == "error"
    assert api.run_status(run("error", api.ERROR_RETRY_S + 1), now, cfg, False) == "start"



# --- highlight runs (F8 through the API) ---------------------------------------------

def seed_done(engine, at=None):
    with Session(engine) as s:
        seed_links(s)
        set_run(s, "TSLA", "done", at or _now() - timedelta(hours=1))
        s.commit()


def test_highlight_run_reports_running_with_links_then_done_with_highlights(engine):
    seed_done(engine)
    highlights = FakeHighlights(wait=True, save=True)
    db = SqliteDb(engine)
    with TestClient(make_app(db=db, highlights=highlights)) as client:
        first = client.get("/graph/TSLA").json()
        second = client.get("/graph/TSLA").json()  # a poll while it runs: no second run
        highlights.gate.set()
        assert highlights.finished.wait(5)
        wait_for_highlights_done()
        done = GraphResponse.model_validate(client.get("/graph/TSLA").json())
    for body in (first, second):
        assert body["status"] == "running" and len(body["links"]) == 4 and body["highlights"] == []
    assert highlights.calls == ["TSLA"] and db.run_sessions == 1  # its own session
    assert done.status == "done"
    assert [(h.target, h.direction, h.event_type) for h in done.highlights] == [("ALB", "may_benefit", "contract")]


def test_highlights_run_at_most_once_per_news_ttl(engine):
    seed_done(engine)
    highlights = FakeHighlights()
    with TestClient(make_app(db=SqliteDb(engine), highlights=highlights)) as client:
        assert client.get("/graph/TSLA").json()["status"] == "running"
        wait_for_highlights_done()
        assert client.get("/graph/TSLA").json()["status"] == "done"
        assert client.get("/graph/TSLA").json()["status"] == "done"
        assert highlights.calls == ["TSLA"]
        # Older than GRAPH_NEWS_TTL_HOURS: the next request starts one new run.
        api._HIGHLIGHTS_DONE["TSLA"] = (_now() - timedelta(hours=Config().graph_news_ttl_hours + 1), True)
        assert client.get("/graph/TSLA").json()["status"] == "running"
        wait_for_highlights_done()
    assert highlights.calls == ["TSLA", "TSLA"]


def test_highlights_false_starts_no_highlight_run_and_is_done_at_once(engine):
    seed_done(engine)
    highlights = FakeHighlights()
    with TestClient(make_app(db=SqliteDb(engine), highlights=highlights)) as client:
        body = client.get("/graph/TSLA?highlights=false").json()
    assert body["status"] == "done" and len(body["links"]) == 4
    assert highlights.calls == []


def test_failed_highlight_run_still_returns_done_links(engine):
    seed_done(engine)
    highlights = FakeHighlights(error=RuntimeError("model down"))
    with TestClient(make_app(db=SqliteDb(engine), highlights=highlights)) as client:
        assert client.get("/graph/TSLA").json()["status"] == "running"
        assert highlights.finished.wait(5)
        wait_for_highlights_done()
        body = client.get("/graph/TSLA").json()
        assert body["status"] == "done" and len(body["links"]) == 4
        assert highlights.calls == ["TSLA"]
        # A failed run is retried after ERROR_RETRY_S, not after the news TTL.
        api._HIGHLIGHTS_DONE["TSLA"] = (_now() - timedelta(seconds=api.ERROR_RETRY_S + 1), False)
        assert client.get("/graph/TSLA").json()["status"] == "running"
        wait_for_highlights_done()
    assert highlights.calls == ["TSLA", "TSLA"]


def test_highlight_builder_that_cannot_start_never_errors(engine, monkeypatch):
    seed_done(engine)

    def broken(*a, **k):
        raise RuntimeError("no event loop")

    monkeypatch.setattr(api, "start_highlight_run", broken)
    with TestClient(make_app(db=SqliteDb(engine))) as client:
        body = client.get("/graph/TSLA").json()
    assert body["status"] == "done" and len(body["links"]) == 4


def test_graph_without_links_starts_no_highlight_run(engine):
    with Session(engine) as s:
        set_run(s, "TSLA", "done", _now())
        s.commit()
    highlights = FakeHighlights()
    with TestClient(make_app(db=SqliteDb(engine), highlights=highlights)) as client:
        assert client.get("/graph/TSLA").json()["status"] == "done"
    assert highlights.calls == []


def test_no_highlight_run_while_links_are_running(engine):
    with Session(engine) as s:
        seed_links(s)
        set_run(s, "TSLA", "running", _now())
        s.commit()
    highlights = FakeHighlights()
    with TestClient(make_app(db=SqliteDb(engine), highlights=highlights)) as client:
        assert client.get("/graph/TSLA").json()["status"] == "running"
    assert highlights.calls == []


def test_real_highlight_builder_through_the_api(engine, tmp_path):
    """The default builder (highlights.build_highlights) with fake inputs, end to end."""
    from functools import partial

    from company_graph import highlights as graph_highlights

    seed_done(engine)
    with Session(engine) as s:
        s.add(GraphEvent(entity_symbol="TSLA", source="news", event_type="product_launch",
                         title="Tesla unveils a new battery pack", url="https://news.example/pack",
                         occurred_at=_now() - timedelta(hours=3)))
        s.commit()

    def model(system, user, response_model, model=None):
        return response_model(involved=[
            graph_highlights.Involvement(symbol="ALB", reason="Albemarle supplies lithium for Tesla's battery packs."),
            graph_highlights.Involvement(symbol="F", reason="Ford sells EVs that compete with Tesla's."),
        ])

    async def no_refresh(session, symbols, **kw):
        return []

    build = partial(graph_highlights.build_highlights, refresh_news=no_refresh, refresh_market=no_refresh,
                    complete=model, price_gateway=None, store=graph_highlights.EvalStore(tmp_path / "evals.json"))
    with TestClient(make_app(db=SqliteDb(engine), highlights=build)) as client:
        assert client.get("/graph/TSLA").json()["status"] == "running"
        wait_for_highlights_done()
        body = GraphResponse.model_validate(client.get("/graph/TSLA").json())
    assert body.status == "done"
    assert sorted((h.target, h.direction) for h in body.highlights) == [("ALB", "may_benefit"), ("F", "may_face_pressure")]
    assert all(h.source_url == "https://news.example/pack" and h.price_change_pct is None for h in body.highlights)
