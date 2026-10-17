"""Tests for GET /graph/{ticker}/board. Same setup as test_api: a throwaway FastAPI app, a SQLite
file behind the AsyncSession shim, a fixed company directory, and a fake build_board."""

import asyncio
import threading
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models.graph_board_run import GraphBoardRun
from backend.models.graph_board_seat import GraphBoardSeat
from backend.models.graph_link_run import GraphLinkRun
from company_graph import api
from company_graph.boards import BoardRunResult
from company_graph.config import Config
from company_graph.schemas import BoardResponse, load_board_fixture
from tests.company_graph.test_api import DIRECTORY, SqliteDb, _now, engine  # noqa: F401 - engine is a fixture

URL = "https://www.sec.gov/Archives/edgar/data/1318605/000000012600000008/xslF345X05/b.xml"


def seed_seats(s: Session, symbol="TSLA"):
    if s.query(GraphBoardSeat).count():
        return  # already seeded (a rebuild over a stale board)
    s.add_all([
        GraphBoardSeat(company_symbol=symbol, person_cik=1002, name="Thaddeus Vexley", raw_name="VEXLEY THADDEUS",
                       role="Chief Executive Officer", is_officer=True, filed_at=date(2026, 9, 20), evidence_url=URL),
        GraphBoardSeat(company_symbol=symbol, person_cik=1001, name="Marigold A Quillfeather",
                       raw_name="QUILLFEATHER MARIGOLD A", role="Director", is_officer=False,
                       filed_at=date(2026, 8, 15), evidence_url=URL),
        # Another company's board: the same person, never mixed into TSLA's answer.
        GraphBoardSeat(company_symbol="NVDA", person_cik=1001, name="Marigold A Quillfeather",
                       raw_name="QUILLFEATHER MARIGOLD A", role="Director", is_officer=False,
                       filed_at=date(2026, 7, 1), evidence_url=URL),
    ])


def set_run(s: Session, symbol: str, status: str, at, error=None):
    row = s.get(GraphBoardRun, symbol) or GraphBoardRun(symbol=symbol)
    row.status, row.fetched_at, row.error = status, at, error
    s.add(row)


class FakeBoardBuild:
    """Stands in for boards.build_board: marks the run running, waits for `gate`, saves seats, ends done or error."""

    def __init__(self, outcome="done", wait=False, seats=True):
        self.outcome, self.seats = outcome, seats
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
        if self.outcome == "done" and self.seats:
            seed_seats(session.s, symbol)
        set_run(session.s, symbol, self.outcome, _now(), "SecRequestError: down" if self.outcome == "error" else None)
        await session.commit()
        self.finished.set()
        return BoardRunResult(symbol=symbol, status=self.outcome)


@pytest.fixture(autouse=True)
def clean_runs():
    for registry in (api._RUNS, api._BOARD_RUNS):
        registry.clear()
    yield
    for registry in (api._RUNS, api._BOARD_RUNS):
        registry.clear()


def make_app(*, cfg=None, db=None, build=None, directory=DIRECTORY):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_config] = lambda: cfg or Config()
    if db is not None:
        app.dependency_overrides[api.get_db] = lambda: db
    app.dependency_overrides[api.get_company_directory] = lambda: directory
    app.dependency_overrides[api.get_board_builder] = lambda: build or FakeBoardBuild()

    def no_links():
        raise AssertionError("the board endpoint must not start a link run")

    app.dependency_overrides[api.get_link_builder] = lambda: no_links
    return app


def wait_for_board_done():
    for _ in range(100):  # the task's done-callback runs on the app's loop
        if not api._BOARD_RUNS:
            return
        threading.Event().wait(0.02)


def test_first_request_runs_then_done_with_members(engine):  # noqa: F811
    build = FakeBoardBuild(wait=True)
    db = SqliteDb(engine)
    with TestClient(make_app(db=db, build=build)) as client:
        first = client.get("/graph/TSLA/board")
        assert first.status_code == 200
        assert first.json() == {"company": {"symbol": "TSLA", "name": "Tesla, Inc."}, "status": "running", "members": []}

        # A poll while the run is going: still running, and no second run.
        assert client.get("/graph/TSLA/board").json()["status"] == "running"
        assert client.get("/graph/tsla/board").json()["status"] == "running"
        assert build.calls == ["TSLA"]

        build.gate.set()
        assert build.finished.wait(5)
        wait_for_board_done()
        done = client.get("/graph/TSLA/board")
    body = BoardResponse.model_validate(done.json())
    assert body.status == "done"
    assert build.calls == ["TSLA"] and db.run_sessions == 1
    assert done.json()["members"] == [
        {"id": "cik-0000001001", "name": "Marigold A Quillfeather", "role": "Director",
         "evidence_url": URL, "filed_at": "2026-08-15"},
        {"id": "cik-0000001002", "name": "Thaddeus Vexley", "role": "Chief Executive Officer",
         "evidence_url": URL, "filed_at": "2026-09-20"},
    ]
    with Session(engine) as s:
        assert s.get(GraphLinkRun, "TSLA") is None  # link-run status is a separate table


def test_fresh_board_is_done_without_a_run(engine):  # noqa: F811
    with Session(engine) as s:
        seed_seats(s)
        set_run(s, "TSLA", "done", _now() - timedelta(days=1))
        s.commit()
    build = FakeBoardBuild()
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        body = client.get("/graph/TSLA/board").json()
    assert body["status"] == "done" and len(body["members"]) == 2
    assert build.calls == []


def test_stale_board_is_returned_while_a_new_run_starts(engine):  # noqa: F811
    with Session(engine) as s:
        seed_seats(s)
        set_run(s, "TSLA", "done", _now() - timedelta(days=30))
        s.commit()
    build = FakeBoardBuild(wait=True)
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        body = client.get("/graph/TSLA/board").json()
        build.gate.set()
        assert build.finished.wait(5)
    assert body["status"] == "running" and len(body["members"]) == 2
    assert build.calls == ["TSLA"]


def test_board_ttl_is_its_own_setting(engine):  # noqa: F811
    with Session(engine) as s:
        set_run(s, "TSLA", "done", _now() - timedelta(days=3))
        s.commit()
    build = FakeBoardBuild()
    cfg = Config(graph_board_ttl_days=1, graph_link_ttl_days=30)
    with TestClient(make_app(cfg=cfg, db=SqliteDb(engine), build=build)) as client:
        assert client.get("/graph/TSLA/board").json()["status"] == "running"
        assert build.finished.wait(5)
    assert build.calls == ["TSLA"]


def test_company_with_no_filings_ends_done_and_empty(engine):  # noqa: F811
    build = FakeBoardBuild(seats=False)
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        assert client.get("/graph/TSLA/board").json()["status"] == "running"
        assert build.finished.wait(5)
        wait_for_board_done()
        body = client.get("/graph/TSLA/board").json()
    assert body == {"company": {"symbol": "TSLA", "name": "Tesla, Inc."}, "status": "done", "members": []}
    assert build.calls == ["TSLA"]


def test_run_in_progress_elsewhere_is_not_started_again(engine):  # noqa: F811
    with Session(engine) as s:
        set_run(s, "TSLA", "running", _now() - timedelta(minutes=1))
        s.commit()
    build = FakeBoardBuild()
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        assert client.get("/graph/TSLA/board").json()["status"] == "running"
    assert build.calls == []


def test_crashed_running_row_is_restarted(engine):  # noqa: F811
    with Session(engine) as s:
        set_run(s, "TSLA", "running", _now() - timedelta(minutes=30))
        s.commit()
    build = FakeBoardBuild()
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        assert client.get("/graph/TSLA/board").json()["status"] == "running"
        assert build.finished.wait(5)
    assert build.calls == ["TSLA"]


def test_run_that_errors_reports_error_with_stored_members_then_retries_later(engine):  # noqa: F811
    with Session(engine) as s:
        seed_seats(s)
        s.commit()
    build = FakeBoardBuild(outcome="error")
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        assert client.get("/graph/TSLA/board").json()["status"] == "running"
        assert build.finished.wait(5)
        wait_for_board_done()
        body = client.get("/graph/TSLA/board").json()
        assert body["status"] == "error" and len(body["members"]) == 2
        assert build.calls == ["TSLA"]  # inside the retry window: no new run
        with Session(engine) as s:
            set_run(s, "TSLA", "error", _now() - timedelta(seconds=api.ERROR_RETRY_S + 60), "SecRequestError: down")
            s.commit()
        build.finished.clear()
        assert client.get("/graph/TSLA/board").json()["status"] == "running"
        assert build.finished.wait(5)
    assert build.calls == ["TSLA", "TSLA"]


def test_link_run_status_does_not_decide_the_board(engine):  # noqa: F811
    with Session(engine) as s:
        s.add(GraphLinkRun(symbol="TSLA", status="done", fetched_at=_now()))
        s.commit()
    build = FakeBoardBuild(wait=True)
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        assert client.get("/graph/TSLA/board").json()["status"] == "running"
        build.gate.set()
        assert build.finished.wait(5)
    assert build.calls == ["TSLA"]


def test_unknown_ticker_is_404(engine):  # noqa: F811
    build = FakeBoardBuild()
    with TestClient(make_app(db=SqliteDb(engine), build=build)) as client:
        resp = client.get("/graph/ZZZZ/board")
    assert resp.status_code == 404
    assert "ZZZZ" in resp.json()["detail"]
    assert build.calls == []


def test_missing_database_url_is_503():
    with TestClient(make_app(cfg=Config(database_url=""))) as client:
        resp = client.get("/graph/TSLA/board")
    assert resp.status_code == 503
    assert "DATABASE_URL" in resp.json()["detail"]


def test_unreachable_database_is_503():
    class DownDb:
        def session(self):
            raise OSError("connection refused")

    with TestClient(make_app(db=DownDb())) as client:
        resp = client.get("/graph/TSLA/board")
    assert resp.status_code == 503 and "unavailable" in resp.json()["detail"]


def test_fake_mode_serves_board_fixtures_without_database_or_network(monkeypatch):
    def no_directory():
        raise AssertionError("fake mode must not load the company directory")

    def boom():
        raise AssertionError("fake mode must not build a board")

    monkeypatch.setattr(api, "get_directory", no_directory)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_config] = lambda: Config(graph_fake=1)
    app.dependency_overrides[api.get_board_builder] = lambda: boom
    with TestClient(app) as client:
        tsla = client.get("/graph/TSLA/board")
        assert tsla.status_code == 200
        assert tsla.json() == load_board_fixture("TSLA").model_dump()
        assert client.get("/graph/tsla/board").json()["company"]["symbol"] == "TSLA"
        shared = {m["id"] for m in tsla.json()["members"]} & {m["id"] for m in client.get("/graph/NVDA/board").json()["members"]}
        assert len(shared) == 1  # the fixture interlock
        assert client.get("/graph/ZZZZ/board").json() == {
            "company": {"symbol": "ZZZZ", "name": "ZZZZ"}, "status": "done", "members": []}
    assert api._DBS == {}
