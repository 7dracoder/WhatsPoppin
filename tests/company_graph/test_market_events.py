import ast
import asyncio
import inspect as pyinspect
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, Table, Text, create_engine, event, select, text
from sqlalchemy.orm import Session

from backend.entities import EntityAlias, load_entities
from backend.models.entity import Entity
from backend.models.graph_event import GraphEvent
from backend.models.market_entity import MarketEntity
from company_graph import market_events
from company_graph.companies import CompanyDirectory
from company_graph.config import Config
from company_graph.db import create_tables
from company_graph.market_events import AlertRow, alert_texts, alert_url, fetch_market_events, find_companies, match_alert

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

SEC_FIXTURE = {str(i): {"cik_str": cik, "ticker": t, "title": name} for i, (cik, t, name) in enumerate([
    (1318605, "TSLA", "Tesla, Inc."),
    (320193, "AAPL", "Apple Inc."),
    (1418121, "APLE", "Apple Hospitality REIT, Inc."),
    (1652044, "GOOGL", "Alphabet Inc."),
    (1045810, "NVDA", "NVIDIA CORP"),
    (63908, "MCD", "MCDONALDS CORP"),
    (21344, "KO", "COCA COLA CO"),
    (200406, "JNJ", "JOHNSON & JOHNSON"),
    (51143, "IBM", "INTERNATIONAL BUSINESS MACHINES CORP"),
    (27419, "TGT", "TARGET CORP"),
    (1751788, "DOW", "Dow Inc."),
    (1403161, "V", "VISA INC."),
    (1, "GLDX", "Gold Corp"),
    (1640147, "SNOW", "Snowflake Inc."),
    (1097864, "ON", "ON SEMICONDUCTOR CORP"),
    (899051, "ALL", "ALLSTATE CORP"),
    (91576, "KEY", "KEYCORP /NEW/"),
    (749251, "IT", "GARTNER INC"),
    (1090872, "A", "AGILENT TECHNOLOGIES, INC."),
    (111, "ACME", "Acme Corp"),
    (222, "ACMY", "Acme Ltd"),
    (1673358, "INTR", "Inter & Co, Inc."),
    (333, "PPHC", "Public Policy Holding Co"),
    (19617, "JPM", "JPMORGAN CHASE & CO"),
    (1800, "ABT", "ABBOTT LABORATORIES"),
])}


@pytest.fixture
def directory():
    return CompanyDirectory.from_sec_json(SEC_FIXTURE)


def symbols(text, directory, aliases=None):
    return [m.company.symbol for m in find_companies(text, directory, aliases)]


# --- finding company names -------------------------------------------------------------

def test_company_named_in_any_kind_of_market_matches(directory):
    assert symbols("Will Tesla deliver 500k vehicles in Q3?", directory) == ["TSLA"]
    # A company in a weather market is still that company.
    assert symbols("Will it rain at Apple's Cupertino campus on Oct 12?", directory) == ["AAPL"]
    assert symbols("Highest temperature in NYC on Oct 10, 2026?", directory) == []


def test_longest_name_wins(directory):
    assert symbols("Will Apple Hospitality REIT cut its dividend?", directory) == ["APLE"]
    assert symbols("Will Apple beat Tesla and Apple again?", directory) == ["AAPL", "TSLA"]


def test_names_with_punctuation(directory):
    assert symbols("Will McDonald's raise prices?", directory) == ["MCD"]
    assert symbols("Will Coca-Cola beat earnings?", directory) == ["KO"]
    assert symbols("Will Johnson & Johnson settle the talc case?", directory) == ["JNJ"]
    assert symbols("Tesla, Inc. deliveries", directory) == ["TSLA"]


def test_short_tickers_never_match_as_words(directory):
    # ON, ALL, KEY, IT, A, SNOW and V are tickers; written as words they are not companies.
    assert symbols("Will IT rain ON ALL of NYC? A KEY day", directory) == []
    assert symbols("Will Snow fall in Denver?", directory) == []
    assert symbols("Will V be the next letter?", directory) == []
    assert symbols("Will ON Semiconductor report?", directory) == ["ON"]  # the full name does


def test_common_word_company_names_do_not_match(directory):
    assert symbols("Will gold hit $3,000?", directory) == []
    assert symbols("Will Gold close above $3,000?", directory) == []
    assert symbols("Will the Dow close above 45,000?", directory) == []
    assert symbols("Will Target hit its goal?", directory) == []
    assert symbols("Will Visa rules for students change?", directory) == []
    assert symbols("Will Public Policy shift?", directory) == []


def test_lower_case_words_and_phrases_do_not_match(directory):
    assert symbols("apple pie contest winner", directory) == []
    assert symbols("Will it snow in the Big Apple on Christmas?", directory) == []
    assert symbols("Will Inter and Milan draw?", directory) == []  # "Inter & Co" needs the "Co"
    assert symbols("Will Inter & Co list in New York?", directory) == ["INTR"]


def test_ambiguous_names_need_a_cashtag(directory):
    assert symbols("Will Acme ship?", directory) == []
    assert symbols("Will $ACME close green?", directory) == ["ACME"]
    assert symbols("Will $TSLA close above $300?", directory) == ["TSLA"]
    assert symbols("Will $NOPE or $300 move?", directory) == []


def test_seed_aliases_extend_names_with_the_same_rules(directory):
    aliases = [EntityAlias(symbol="GOOGL", aliases=["Google", "Alphabet"]),
               EntityAlias(symbol="IBM", aliases=["IBM"]),
               EntityAlias(symbol="JPM", aliases=["JPMorgan", "Chase"]),
               EntityAlias(symbol="ABT", aliases=["Abbott"]),
               EntityAlias(symbol="ZZZZ", aliases=["Nowhere Corp"])]  # not in the directory
    assert symbols("Which company has the best AI model? Google", directory, aliases) == ["GOOGL"]
    assert symbols("Will IBM announce a quantum computer?", directory, aliases) == ["IBM"]
    assert symbols("Will ibm announce?", directory, aliases) == []
    assert symbols("Will JPMorgan raise rates?", directory, aliases) == ["JPM"]
    assert symbols("Will Chase Elliott win at Talladega?", directory, aliases) == []
    assert symbols("Will Greg Abbott win reelection?", directory, aliases) == []
    assert symbols("Will Nowhere Corp rally?", directory, aliases) == []
    assert symbols("Google", directory) == []  # without aliases, "Google" is not an SEC name


def test_team_seed_file_loads_and_matches(directory):
    assert symbols("Will Nvidia or Google lead?", directory, load_entities()) == ["NVDA", "GOOGL"]


# --- alerts -> events ------------------------------------------------------------------

def alert(alert_id=1, title="Will Tesla deliver 500k vehicles in Q3?", url="https://kalshi.com/markets/kxtsla",
          market_row=True, created_at=NOW - timedelta(days=1), **ctx):
    market = {"title": title, "event_title": ctx.get("event_title"), "outcome_label": ctx.get("outcome_label"),
              "source": "kalshi", "url": url}
    return AlertRow(alert_id=alert_id, created_at=created_at, source="kalshi", market_id=f"M{alert_id}",
                    summary=f"[Kalshi] {title}: YES rose from 0.420 to 0.610 (+19.0 pts) in 5 min.",
                    context={"market": market},
                    market_title=title if market_row else None, market_url=url if market_row else None)


def test_alert_naming_a_listed_company_gives_one_event(directory):
    events = match_alert(alert(), directory)
    assert len(events) == 1
    ev = events[0]
    assert (ev.entity_symbol, ev.alert_id, ev.url, ev.source, ev.market_id) == (
        "TSLA", 1, "https://kalshi.com/markets/kxtsla", "kalshi", "M1")
    assert ev.title.startswith("[Kalshi] Will Tesla")
    assert ev.occurred_at == NOW - timedelta(days=1)


def test_alert_naming_no_listed_company_gives_none(directory):
    assert match_alert(alert(title="Highest temperature in NYC on Oct 10, 2026?"), directory) == []


def test_event_title_and_outcome_label_are_searched(directory):
    row = alert(title="Above $5T?", event_title="Nvidia market cap on Dec 31", outcome_label="Tesla")
    assert {e.entity_symbol for e in match_alert(row, directory, [EntityAlias(symbol="NVDA", aliases=["Nvidia"])])} \
        == {"NVDA", "TSLA"}
    assert alert_texts(row) == ["Above $5T?", "Nvidia market cap on Dec 31", "Tesla"]


def test_url_falls_back_to_alert_context():
    row = alert(market_row=False)
    assert alert_url(row) == "https://kalshi.com/markets/kxtsla"
    assert alert_url(alert(url=None)) is None


def test_context_as_json_text_is_read():
    row = AlertRow(1, NOW, "kalshi", "M1", "s", '{"market": {"title": "Tesla", "url": "https://x"}}')
    assert alert_texts(row) == ["Tesla"]
    assert alert_url(row) == "https://x"
    assert alert_texts(AlertRow(1, NOW, "kalshi", "M1", "s", "not json")) == []


# --- database (SQLite stand-ins; reads only from alerts and markets) ------------------

PROTECTED = ("alerts", "markets", "market_prices", "market_trades", "market_hourly", "market_baselines")
_WRITE = re.compile(r"^\s*(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+\"?(\w+)", re.I)

stand_ins = MetaData()
alerts_table = Table(  # SQLite cannot hold ARRAY/JSONB; same names for the columns this feature reads
    "alerts", stand_ins,
    Column("id", Integer, primary_key=True), Column("created_at", DateTime(timezone=True)),
    Column("status", Text), Column("claimed_at", DateTime(timezone=True)), Column("source", Text),
    Column("market_id", Text), Column("summary", Text), Column("context", JSON))
markets_table = Table(
    "markets", stand_ins,
    Column("source", Text, primary_key=True), Column("market_id", Text, primary_key=True),
    Column("title", Text), Column("outcome_label", Text), Column("event_title", Text), Column("url", Text))


class AsyncSessionShim:
    """The AsyncSession calls market_events makes, run on a sync SQLite Session."""

    def __init__(self, session):
        self.sync = session

    async def execute(self, stmt):
        return self.sync.execute(stmt)

    async def get(self, model, pk):
        return self.sync.get(model, pk)

    def add(self, obj):
        self.sync.add(obj)

    async def flush(self):
        self.sync.flush()


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    statements: list[str] = []
    with engine.begin() as conn:
        stand_ins.create_all(conn)
        create_tables(conn)
        conn.execute(markets_table.insert(), [
            {"source": "kalshi", "market_id": "TSLA-1", "title": "Will Tesla deliver 500k vehicles in Q3?",
             "outcome_label": None, "url": "https://kalshi.com/markets/kxtsla"},
            {"source": "kalshi", "market_id": "NYC-1", "title": "Highest temperature in NYC on Oct 10?",
             "outcome_label": None, "url": "https://kalshi.com/markets/kxhighny"},
            {"source": "polymarket", "market_id": "0xabc", "title": "Which company is largest at year end?",
             "outcome_label": "Apple", "url": "https://polymarket.com/event/largest-company"},
        ])
        conn.execute(alerts_table.insert(), [
            _alert_row(1, NOW - timedelta(days=10), "kalshi", "TSLA-1", "old Tesla move"),  # outside window
            _alert_row(2, NOW - timedelta(days=2), "kalshi", "TSLA-1", "Tesla move two days ago"),
            _alert_row(3, NOW - timedelta(days=1), "kalshi", "NYC-1", "NYC weather move"),
            _alert_row(4, NOW - timedelta(hours=3), "polymarket", "0xabc", "Largest company move"),
            _alert_row(5, NOW - timedelta(hours=1), "kalshi", "TSLA-1", "Tesla move an hour ago"),
            _alert_row(6, NOW - timedelta(hours=2), "kalshi", "GONE-1", "Tesla market without a markets row",
                       title="Will Tesla recall the Cybertruck?", url=None),
        ])
    event.listen(engine, "before_cursor_execute",
                 lambda conn, cursor, statement, *a: statements.append(statement))
    return engine, statements


def _alert_row(alert_id, created_at, source, market_id, summary, title=None, url="missing"):
    market = {"title": title} if title else {}
    if url != "missing":
        market["url"] = url
    return {"id": alert_id, "created_at": created_at, "status": "pending", "claimed_at": None, "source": source,
            "market_id": market_id, "summary": summary, "context": {"market": market}}


def run(engine, **kwargs):
    with Session(engine) as session:
        events = asyncio.run(fetch_market_events(session=AsyncSessionShim(session), now=NOW, cfg=Config(), **kwargs))
        session.commit()
    return events


def rows(engine, model):
    with Session(engine) as session:
        return session.scalars(select(model)).all()


def test_fetch_stores_one_event_per_company_and_market(db, directory):
    engine, _ = db
    events = run(engine, directory=directory, aliases=[])
    assert {(e.entity_symbol, e.alert_id) for e in events} == {("TSLA", 2), ("TSLA", 5), ("AAPL", 4), ("TSLA", 6)}

    graph_events = {(g.entity_symbol, g.url): g for g in rows(engine, GraphEvent)}
    assert set(graph_events) == {("TSLA", "https://kalshi.com/markets/kxtsla"),
                                 ("AAPL", "https://polymarket.com/event/largest-company")}
    tesla = graph_events[("TSLA", "https://kalshi.com/markets/kxtsla")]
    assert (tesla.source, tesla.event_type, tesla.title, tesla.alert_id) == (
        "market", "odds_move", "Tesla move an hour ago", 5)  # the newest alert on that market
    assert graph_events[("AAPL", "https://polymarket.com/event/largest-company")].alert_id == 4

    assert {(m.source, m.market_id, m.entity_symbol) for m in rows(engine, MarketEntity)} == {
        ("kalshi", "TSLA-1", "TSLA"), ("polymarket", "0xabc", "AAPL"), ("kalshi", "GONE-1", "TSLA")}
    assert {(e.symbol, e.type) for e in rows(engine, Entity)} == {("TSLA", "company"), ("AAPL", "company")}


def test_fetch_twice_does_not_duplicate(db, directory):
    engine, _ = db
    run(engine, directory=directory, aliases=[])
    run(engine, directory=directory, aliases=[])
    assert len(rows(engine, GraphEvent)) == 2
    assert len(rows(engine, MarketEntity)) == 3


def test_fetch_for_given_symbols_stores_only_those(db, directory):
    engine, _ = db
    events = run(engine, symbols=["AAPL", "MSFT"], directory=directory, aliases=[])
    assert [(e.entity_symbol, e.alert_id) for e in events] == [("AAPL", 4)]
    assert [g.entity_symbol for g in rows(engine, GraphEvent)] == ["AAPL"]
    assert [e.symbol for e in rows(engine, Entity)] == ["AAPL"]


def test_alert_naming_no_company_stores_nothing(db, directory):
    engine, _ = db
    run(engine, symbols=["NVDA"], directory=directory, aliases=[])
    assert rows(engine, GraphEvent) == [] and rows(engine, MarketEntity) == []


def test_nothing_writes_to_alerts_or_market_tables(db, directory):
    engine, statements = db
    with engine.connect() as conn:
        before = conn.execute(text("SELECT * FROM alerts ORDER BY id")).all()
        markets_before = conn.execute(text("SELECT * FROM markets ORDER BY market_id")).all()
    run(engine, directory=directory, aliases=[])

    for probe in ("UPDATE alerts SET status=?", "DELETE FROM markets", 'INSERT INTO "alerts" (id) VALUES (?)'):
        assert _WRITE.match(probe).group(1) in PROTECTED  # the detector below would catch a write
    written = {m.group(1).lower() for s in statements if (m := _WRITE.match(s))}
    assert written, "the run should have written this feature's tables"
    assert written <= {"entities", "market_entities", "graph_events"}
    assert not written & set(PROTECTED)
    assert any(re.search(r"\bFROM alerts\b", s) for s in statements)  # it did read alerts
    with engine.connect() as conn:
        assert conn.execute(text("SELECT * FROM alerts ORDER BY id")).all() == before  # status, claimed_at intact
        assert conn.execute(text("SELECT * FROM markets ORDER BY market_id")).all() == markets_before


def test_module_has_no_write_statements_for_protected_tables():
    source = pyinspect.getsource(market_events)
    tree = ast.parse(source)
    imported = {a.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith("sqlalchemy") for a in node.names}
    assert not imported & {"insert", "update", "delete", "text"}
    assert "claimed_at" not in source and ".status" not in source
    assert "session.add(Alert" not in source and "session.add(Market(" not in source
    query = str(market_events.recent_alerts_query(NOW))
    assert query.lstrip().upper().startswith("SELECT")


def test_fake_mode_without_session_reads_nothing():
    assert asyncio.run(fetch_market_events(["TSLA"], cfg=Config(graph_fake=1))) == []


def test_without_session_needs_database_url(directory):
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        asyncio.run(fetch_market_events(["TSLA"], cfg=Config(), directory=directory, aliases=[]))
