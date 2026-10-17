"""Tests for company_graph.highlights (F8). No network, no real model, no Postgres: links come from the
TSLA fixture, the model is a fake `complete`, news/market refreshes and Alpaca are fakes, and the
database is SQLite behind a small AsyncSession shim."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.ingestion.alpaca import AlpacaApiError, PricePoint, ZoomTier
from backend.models.entity import Entity
from backend.models.entity_relationship import EntityRelationship
from backend.models.graph_event import GraphEvent
from backend.models.graph_highlight import GraphHighlight
from company_graph import highlights as hl
from company_graph.config import Config
from company_graph.db import create_tables
from company_graph.llm import LlmError
from company_graph.schemas import load_fixture

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
CFG = Config()
LAUNCH_URL = "https://news.example/tesla-model-2-launch"


class AsyncSessionShim:
    def __init__(self, session):
        self.s = session
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model, pk):
        return self.s.get(model, pk)

    def add(self, row):
        self.s.add(row)

    async def execute(self, stmt):
        return self.s.execute(stmt)

    async def flush(self):
        self.s.flush()

    async def commit(self):
        self.commits += 1
        self.s.commit()

    async def rollback(self):
        self.rollbacks += 1
        self.s.rollback()


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        create_tables(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine, expire_on_commit=False) as s:
        yield AsyncSessionShim(s)


@pytest.fixture
def store(tmp_path):
    return hl.EvalStore(tmp_path / "highlight_evals.json")


def seed_fixture_links(s: Session, ticker="TSLA"):
    """entity_relationships rows from the fixture's links (sector peers as source 'sector')."""
    fixture = load_fixture(ticker)
    s.add(Entity(symbol=ticker, name=fixture.company.name, type="company"))
    for n in fixture.nodes:
        s.add(Entity(symbol=n.symbol, name=n.name, type="company"))
    s.flush()
    for link in fixture.links:
        sector = link.type == "sector_peer"
        s.add(EntityRelationship(entity_symbol=ticker, related_entity_symbol=link.target, relationship_type=link.type,
                                 confidence=0.3 if sector else 0.9, source="sector" if sector else "filing",
                                 summary=link.summary, evidence_url=link.evidence_url))
    s.commit()


def add_event(s: Session, symbol="TSLA", url=LAUNCH_URL, title="Tesla unveils the lower-priced Model 2",
              event_type="product_launch", hours_ago=20):
    ev = GraphEvent(entity_symbol=symbol, source="news", event_type=event_type, title=title, url=url,
                    occurred_at=NOW - timedelta(hours=hours_ago))
    s.add(ev)
    s.commit()
    return ev


class FakeModel:
    """Stands in for llm.complete: answers with `answers(user_prompt)` and records each prompt."""

    def __init__(self, answers):
        self.answers = answers
        self.prompts: list[str] = []

    def __call__(self, system, user, response_model, model=None):
        assert response_model is hl.EventInvolvement
        self.prompts.append(user)
        out = self.answers(user)
        if isinstance(out, Exception):
            raise out
        return hl.EventInvolvement(involved=[hl.Involvement(**i) for i in out])


def launch_answer(_prompt):
    return [
        {"symbol": "PCRFY", "reason": "Tesla's new Model 2 uses battery cells from Panasonic, a named cell supplier."},
        {"symbol": "GM", "reason": "Tesla launched the Model 2 in a segment where GM sells a competing EV."},
        {"symbol": "RIVN", "reason": "Rivian is another electric-vehicle maker.", "competitive_gain": False},
        {"symbol": "XYZ", "reason": "Not a candidate."},
    ]


class NoRefresh:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def __call__(self, session, symbols, **kwargs):
        self.calls.append(list(symbols))
        if self.error:
            raise self.error
        return []


def run(session, store, **kw):
    kw.setdefault("cfg", CFG)
    kw.setdefault("now", NOW)
    kw.setdefault("refresh_news", NoRefresh())
    kw.setdefault("refresh_market", NoRefresh())
    kw.setdefault("price_gateway", None)
    return asyncio.run(hl.build_highlights("TSLA", session=session, store=store, **kw))


def saved(session):
    return session.s.execute(select(GraphHighlight).order_by(GraphHighlight.target_symbol)).scalars().all()


# --- Done when ------------------------------------------------------------------------

def test_launch_event_highlights_only_relevant_neighbors(session, store):
    seed_fixture_links(session.s)
    add_event(session.s)
    model = FakeModel(launch_answer)
    result = run(session, store, complete=model)

    rows = saved(session)
    assert [(r.target_symbol, r.direction) for r in rows] == [("GM", "may_face_pressure"), ("PCRFY", "may_benefit")]
    assert all(r.source_url == LAUNCH_URL and r.source_symbol == "TSLA" for r in rows)
    assert all(r.price_change_pct is None for r in rows)  # no Alpaca gateway
    assert rows[1].reason.startswith("Tesla's new Model 2 uses battery cells")
    assert result.saved == 2 and result.evaluated == 1 and len(model.prompts) == 1
    # Every linked company was offered to the model, with its role.
    prompt = model.prompts[0]
    for sym in ("PCRFY", "CATL", "NVDA", "ALB", "GM", "F", "RIVN", "LCID"):
        assert f"- {sym} (" in prompt
    assert "competes with" in prompt and "supplies" in prompt and "same industry" in prompt
    assert "Tesla unveils the lower-priced Model 2" in prompt


def test_no_events_saves_nothing_and_asks_no_model(session, store):
    seed_fixture_links(session.s)

    def boom(*a, **k):
        raise AssertionError("no events: the model must not be called")

    news, market = NoRefresh(), NoRefresh()
    result = run(session, store, complete=boom, refresh_news=news, refresh_market=market)
    assert saved(session) == [] and result.events == 0 and result.evaluated == 0
    # The refreshes were still tried, for the company and every linked company.
    assert len(news.calls) == 1 and news.calls[0][0] == "TSLA"
    assert sorted(news.calls[0][1:]) == ["ALB", "CATL", "F", "GM", "LCID", "NVDA", "PCRFY", "RIVN"]
    assert market.calls == news.calls


def test_failed_price_call_leaves_price_null(session, store):
    seed_fixture_links(session.s)
    add_event(session.s)

    def failing_fetch(ticker, tier, gateway, now):
        raise AlpacaApiError("500", "server error", retryable=True)

    result = run(session, store, complete=FakeModel(launch_answer), price_gateway=object(), fetch_prices=failing_fetch)
    rows = saved(session)
    assert len(rows) == 2 and all(r.price_change_pct is None for r in rows)
    assert result.saved == 2


# --- links, refresh steps, directions -------------------------------------------------

def test_no_links_does_nothing(session, store):
    news, market = NoRefresh(), NoRefresh()

    def boom(*a, **k):
        raise AssertionError("no links: the model must not be called")

    result = run(session, store, complete=boom, refresh_news=news, refresh_market=market)
    assert result.linked == 0 and news.calls == [] and market.calls == []


def test_failed_refresh_steps_are_skipped(session, store):
    seed_fixture_links(session.s)
    add_event(session.s)
    result = run(session, store, complete=FakeModel(launch_answer),
                 refresh_news=NoRefresh(RuntimeError("NEWSAPI_KEY is not set")),
                 refresh_market=NoRefresh(Exception("no such table: alerts")))
    assert result.steps_failed == ["news", "market"]
    assert session.rollbacks == 2
    assert len(saved(session)) == 2


def test_default_news_step_without_key_is_skipped_and_calls_nothing(session, store, monkeypatch):
    import company_graph.news_events as ne

    async def no_news(*a, **k):
        raise AssertionError("no NEWSAPI_KEY: refresh_news_events must not run")

    monkeypatch.setattr(ne, "refresh_news_events", no_news)
    seed_fixture_links(session.s)
    result = run(session, store, refresh_news=hl._default_news, complete=FakeModel(launch_answer))
    assert result.steps_failed == ["news"]


def test_event_about_a_linked_company_targets_the_searched_company(session, store):
    seed_fixture_links(session.s)
    add_event(session.s, symbol="PCRFY", url="https://news.example/panasonic-plant",
              title="Panasonic opens a new battery plant in Kansas")
    model = FakeModel(lambda p: [{"symbol": "TSLA", "reason": "Panasonic's new plant makes cells for Tesla."}])
    run(session, store, complete=model)
    rows = saved(session)
    assert [(r.source_symbol, r.target_symbol, r.direction) for r in rows] == [("PCRFY", "TSLA", "may_benefit")]
    # Only the searched company is offered, as Panasonic's customer.
    assert "- TSLA (" in model.prompts[0] and "is a customer of Panasonic" in model.prompts[0]
    assert "- GM (" not in model.prompts[0]


def test_event_about_a_competitor_puts_pressure_on_the_searched_company(session, store):
    seed_fixture_links(session.s)
    add_event(session.s, symbol="GM", url="https://news.example/gm-ev", title="GM launches a $25,000 EV")
    run(session, store, complete=FakeModel(lambda p: [{"symbol": "TSLA", "reason": "GM launched an EV that competes with Tesla's."}]))
    assert [(r.target_symbol, r.direction) for r in saved(session)] == [("TSLA", "may_face_pressure")]


def test_sector_peer_only_on_a_clear_competitive_gain(session, store):
    seed_fixture_links(session.s)
    add_event(session.s, title="Tesla wins a 10,000-vehicle fleet order from Hertz", event_type="contract")
    model = FakeModel(lambda p: [
        {"symbol": "RIVN", "reason": "Tesla won a fleet order that Rivian also bid for.", "competitive_gain": True},
        {"symbol": "LCID", "reason": "Lucid also makes electric cars.", "competitive_gain": False},
    ])
    run(session, store, complete=model)
    assert [(r.target_symbol, r.direction) for r in saved(session)] == [("RIVN", "may_face_pressure")]


def test_direction_for():
    assert hl.direction_for("supplier") == hl.direction_for("customer") == hl.direction_for("partner") == "may_benefit"
    assert hl.direction_for("competitor") == "may_face_pressure"
    assert hl.direction_for("competitor", competitive_gain=True) == "may_face_pressure"
    assert hl.direction_for("sector_peer") is None
    assert hl.direction_for("sector_peer", competitive_gain=True) == "may_face_pressure"


def test_non_factual_reasons_are_dropped(session, store):
    seed_fixture_links(session.s)
    add_event(session.s)
    run(session, store, complete=FakeModel(lambda p: [
        {"symbol": "PCRFY", "reason": "Panasonic shares will rise; investors should buy."},
        {"symbol": "ALB", "reason": "Albemarle supplies lithium for Tesla's battery cells."},
    ]))
    assert [r.target_symbol for r in saved(session)] == ["ALB"]
    assert hl.is_factual("Tesla named Panasonic as its cell supplier for the Model 2.")
    assert not hl.is_factual("The stock could climb after the launch.")
    assert not hl.is_factual("A price target of $300.")
    assert not hl.is_factual("  ")
    assert not hl.is_factual("The index batch is unrelated to NVIDIA's supply relationship with Nebius.")
    assert not hl.is_factual("The launch does not involve Panasonic's cells.")


def test_model_failure_on_one_event_does_not_fail_the_run(session, store):
    seed_fixture_links(session.s)
    add_event(session.s)
    add_event(session.s, url="https://news.example/recall", title="Tesla recalls 10,000 cars", event_type="recall",
              hours_ago=5)

    def answers(prompt):
        if "recalls" in prompt:
            return LlmError("upstream 500")
        return launch_answer(prompt)

    result = run(session, store, complete=FakeModel(answers))
    assert result.model_errors == 1 and result.saved == 2
    # The failed event is not remembered, so the next run asks again.
    model = FakeModel(lambda p: [])
    run(session, store, complete=model)
    assert len(model.prompts) == 1 and "recalls" in model.prompts[0]


# --- no repeats ----------------------------------------------------------------------

def test_judged_events_are_not_asked_again_and_highlights_not_duplicated(session, store, tmp_path):
    seed_fixture_links(session.s)
    add_event(session.s)
    run(session, store, complete=FakeModel(launch_answer))

    def boom(*a, **k):
        raise AssertionError("already judged")

    # Same store object, and a fresh one read back from the file.
    second = run(session, store, complete=boom)
    third = run(session, hl.EvalStore(tmp_path / "highlight_evals.json"), complete=boom)
    assert second.reused == 1 and third.reused == 1
    assert len(saved(session)) == 2


def test_new_linked_company_triggers_one_new_question_without_duplicates(session, store):
    seed_fixture_links(session.s)
    add_event(session.s)
    run(session, store, complete=FakeModel(launch_answer))
    session.s.add(Entity(symbol="TM", name="Toyota Motor Corp", type="company"))
    session.s.add(EntityRelationship(entity_symbol="TSLA", related_entity_symbol="TM", relationship_type="competitor",
                                     confidence=0.9, source="filing", summary="Tesla names Toyota.",
                                     evidence_url="https://example.com/fixture/sec/tsla-10k#competition"))
    session.s.commit()
    model = FakeModel(lambda p: launch_answer(p) + [{"symbol": "TM", "reason": "Toyota sells a competing small car."}])
    run(session, store, complete=model, cfg=Config(graph_max_linked=20))
    assert len(model.prompts) == 1
    assert [r.target_symbol for r in saved(session)] == ["GM", "PCRFY", "TM"]


def test_eval_store_prunes_and_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "evals.json"
    path.write_text("{not json")
    store = hl.EvalStore(path)
    store.record("TSLA", "TSLA", "u1", ["A", "B"], NOW - timedelta(days=10))
    store.record("TSLA", "TSLA", "u2", ["A"], NOW)
    store.prune(NOW, CFG.event_window_s)
    store.save()
    again = hl.EvalStore(path)
    assert not again.judged("TSLA", "TSLA", "u1", ["A"])
    assert again.judged("TSLA", "TSLA", "u2", ["A"]) and not again.judged("TSLA", "TSLA", "u2", ["A", "C"])


# --- saving --------------------------------------------------------------------------

def test_save_drops_rows_without_url_and_duplicates(session):
    ev = add_event(session.s)

    def h(target, url=LAUNCH_URL):
        return hl.NewHighlight(event_id=ev.id, source_symbol="TSLA", target_symbol=target, direction="may_benefit",
                               reason="r", source_url=url, event_time=NOW)

    rows = asyncio.run(hl.save_highlights(session, [h("A"), h("B", url=None), h("C", url="  "), h("A")]))
    assert [r.target_symbol for r in rows] == ["A"]
    assert asyncio.run(hl.save_highlights(session, [h("A")])) == []


def test_events_outside_the_window_are_ignored(session, store):
    seed_fixture_links(session.s)
    add_event(session.s, hours_ago=24 * 8)

    def boom(*a, **k):
        raise AssertionError("old event")

    assert run(session, store, complete=boom).events == 0


def test_fake_mode_does_nothing(session, store):
    seed_fixture_links(session.s)
    add_event(session.s)
    result = run(session, store, cfg=Config(graph_fake=1), complete=None)
    assert result.linked == 0 and saved(session) == []


# --- prices --------------------------------------------------------------------------

def pts(*pairs):
    return [PricePoint(market_id="X", price_or_odds=p, timestamp=t) for t, p in pairs]


def test_price_change_from_just_before_the_event_to_latest(session, store):
    seed_fixture_links(session.s)
    add_event(session.s, hours_ago=20)  # inside DAILY (24 h), outside RECENT (6 h)
    calls = []

    def fetch(ticker, tier, gateway, now):
        calls.append((ticker, tier))
        return pts((NOW - timedelta(hours=23), 100.0), (NOW - timedelta(hours=21), 110.0),
                   (NOW - timedelta(hours=19), 120.0), (NOW - timedelta(minutes=20), 121.0))

    run(session, store, complete=FakeModel(launch_answer), price_gateway=object(), fetch_prices=fetch)
    rows = {r.target_symbol: r for r in saved(session)}
    assert float(rows["GM"].price_change_pct) == pytest.approx(10.0)  # 110 -> 121
    # PCRFY is in the SEC list in real life but here no directory is given; both are US-ticker shaped.
    assert sorted(calls) == [("GM", ZoomTier.DAILY), ("PCRFY", ZoomTier.DAILY)]


def test_pick_tier_and_percent_change():
    assert hl.pick_tier(NOW - timedelta(hours=1), NOW)[0] == ZoomTier.RECENT
    assert hl.pick_tier(NOW - timedelta(hours=20), NOW)[0] == ZoomTier.DAILY
    assert hl.pick_tier(NOW - timedelta(days=3), NOW)[:2] == [ZoomTier.WEEKLY, ZoomTier.MONTHLY]
    assert hl.pick_tier(NOW - timedelta(days=20), NOW)[0] == ZoomTier.MONTHLY
    assert hl.percent_change([], NOW) is None
    assert hl.percent_change(pts((NOW + timedelta(hours=1), 5.0)), NOW) is None  # no bar before the event
    assert hl.percent_change(pts((NOW - timedelta(hours=1), 50.0), (NOW + timedelta(hours=1), 45.0)), NOW) == -10.0


def test_price_is_null_for_names_missing_keys_and_unknown_symbols(monkeypatch):
    def fetch(*a):
        raise AssertionError("must not fetch")

    t = NOW - timedelta(hours=2)
    assert asyncio.run(hl.price_change_pct("Contemporary Amperex Technology", t, NOW, object(), fetch)) is None
    assert asyncio.run(hl.price_change_pct("GM", t, NOW, None, fetch)) is None

    class Directory:
        def get(self, symbol):
            return None

    assert asyncio.run(hl.price_change_pct("CATL", t, NOW, object(), fetch, directory=Directory())) is None
    assert hl.alpaca_gateway_from_env() is None  # conftest clears the Alpaca keys


def test_price_falls_back_one_tier_when_the_range_has_no_bar_before_the_event():
    tiers = []

    def fetch(ticker, tier, gateway, now):
        tiers.append(tier)
        if tier == ZoomTier.WEEKLY:
            return pts((NOW - timedelta(days=2), 10.0))
        return pts((NOW - timedelta(days=8), 8.0), (NOW - timedelta(days=2), 10.0))

    pct = asyncio.run(hl.price_change_pct("GM", NOW - timedelta(days=6), NOW, object(), fetch))
    assert pct == 25.0 and tiers == [ZoomTier.WEEKLY, ZoomTier.MONTHLY]
