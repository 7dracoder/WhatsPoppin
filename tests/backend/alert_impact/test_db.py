import asyncio
from datetime import datetime, timezone

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from backend.alert_impact import db
from backend.alert_impact.match import Impact
from backend.models.alert_impact import AlertImpact
from backend.models.alert_impact_run import AlertImpactRun

NOW = datetime(2026, 10, 10, 22, tzinfo=timezone.utc)
ALERT = {"alert_id": 7, "source": "kalshi", "market_id": "KXB200-1", "url": "https://kalshi.com/markets/kxb200",
         "event_title": "NVIDIA B200 price", "title": None, "created_at": NOW}


class _Result(list):
    def scalars(self):
        return iter(self)


class _Conn:
    def __init__(self, sql):
        self.sql = sql

    async def execute(self, stmt):
        self.sql.append(str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
        return _Result()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class RecordingEngine:
    def __init__(self):
        self.sql = []

    def begin(self):
        return _Conn(self.sql)

    connect = begin


def test_tables_have_no_foreign_keys_to_tables_reset_db_drops():
    for model in (AlertImpact, AlertImpactRun):
        assert not model.__table__.foreign_keys
        assert str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))


def test_save_impacts_writes_impacts_and_graph_events_for_tier_one_only():
    engine = RecordingEngine()
    asyncio.run(db.save_impacts(engine, ALERT, [Impact("NVDA", 1, ["NVDA"]), Impact("TSM", 2, ["NVDA"], 0.9)]))
    delete, impacts, events = engine.sql
    assert delete.startswith("DELETE FROM alert_impacts") and "alert_impacts.alert_id = 7" in delete
    assert "'NVDA', 1" in impacts and "'TSM', 2" in impacts
    assert "INSERT INTO graph_events" in events and "'NVDA', 'market', 'odds_move'" in events
    assert "'TSM'" not in events  # the highlight builder spreads it to linked companies itself
    assert "ON CONFLICT (entity_symbol, url) DO UPDATE" in events and "graph_events.source = 'market'" in events


def test_no_market_url_means_no_graph_event():
    engine = RecordingEngine()
    asyncio.run(db.save_impacts(engine, {**ALERT, "url": None}, [Impact("NVDA", 1, ["NVDA"])]))
    assert not any("graph_events" in s for s in engine.sql)


def test_failed_runs_count_attempts_and_are_retried():
    engine = RecordingEngine()
    asyncio.run(db.save_run(engine, 7, "failed", error="402"))
    assert "alert_impact_runs.attempts + 1" in engine.sql[0]
    engine = RecordingEngine()
    asyncio.run(db.alerts_to_process(engine, NOW))
    assert f"alert_impact_runs.attempts < {db.MAX_ATTEMPTS}" in engine.sql[0]
    assert "alert_impact_runs.status IS NULL" in engine.sql[0]
