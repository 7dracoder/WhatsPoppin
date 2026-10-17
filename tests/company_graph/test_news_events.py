import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import backend.classification.classifier as jev_classifier
from backend.classification import JevError
from backend.classification.primitives import ChoiceAnswer
from backend.entities import EntityAlias
from backend.ingestion.news_api import NewsApiError
from backend.models.graph_event import GraphEvent
from company_graph import news_events as ne
from company_graph.companies import CompanyDirectory
from company_graph.config import Config
from company_graph.db import create_tables
from company_graph.schemas import NEWS_EVENT_TYPES

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
CFG = Config(newsapi_key="test-key")

DIRECTORY = CompanyDirectory.from_sec_json({
    "0": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
    "1": {"cik_str": 1, "ticker": "PCRFY", "title": "Panasonic Holdings Corp"},
    "2": {"cik_str": 2, "ticker": "F", "title": "Ford Motor Co"},
    "3": {"cik_str": 1090872, "ticker": "A", "title": "AGILENT TECHNOLOGIES, INC."},
})


def article(title, url, content=None, hours_ago=30, outlet="Reuters"):
    return {
        "source": {"id": None, "name": outlet},
        "author": None,
        "title": title,
        "url": url,
        "content": content,
        "publishedAt": (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
    }


class FakeGateway:
    """Stands in for NewsApiGateway: records each request and returns canned raw articles."""

    def __init__(self, articles=(), error=None):
        self.articles = list(articles)
        self.error = error
        self.calls = []

    def fetch_raw(self, filters):
        self.calls.append(filters)
        if self.error:
            raise self.error
        return list(self.articles)


def fake_jev(labeler):
    """A call_jev stand-in: answers the single choice question with labeler(state)."""

    def call(state, questions, *args, **kwargs):
        q = questions["main"]
        assert set(q.criteria) == set(NEWS_EVENT_TYPES) | {"none"}
        label = labeler(state)
        probs = {k: (0.9 if k == label else 0.1 / (len(q.criteria) - 1)) for k in q.criteria}
        return {"main": ChoiceAnswer(choice=label, probabilities=probs, confidence=0.8)}

    return call


KEYWORDS = [  # a crude stand-in for Jev's judgement, enough to exercise the label mapping
    ("opinion", "none"), ("why ", "none"), ("stocks to", "none"), ("recall", "recall"),
    ("acquire", "acquisition"), ("buy ", "acquisition"), ("merge", "acquisition"),
    ("beats", "earnings_surprise"), ("misses", "earnings_surprise"), ("earnings", "earnings_surprise"),
    ("contract", "contract"), ("deal", "contract"), ("order", "contract"),
    ("unveil", "product_launch"), ("launch", "product_launch"),
]


def keyword_label(state):
    s = state.lower()
    return next((label for kw, label in KEYWORDS if kw in s), "none")


@pytest.fixture
def jev(monkeypatch):
    monkeypatch.setattr(jev_classifier, "call_jev", fake_jev(keyword_label))


@pytest.fixture
def store(tmp_path):
    return ne.NewsStore(tmp_path / "news_cache.json")


def fetch(symbols, gateway, store, now=NOW, cfg=CFG):
    return asyncio.run(ne.fetch_news_events(symbols, cfg=cfg, store=store, gateway=gateway,
                                            aliases=DIRECTORY.aliases_for, now=now))


class FakeAsyncSession:
    """AsyncSession-shaped wrapper over an in-memory SQLite session (no real database)."""

    def __init__(self, sync_session):
        self.sync = sync_session

    def add(self, obj):
        self.sync.add(obj)

    async def execute(self, stmt):
        return self.sync.execute(stmt)

    async def flush(self):
        self.sync.flush()


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        create_tables(conn)
    with Session(engine) as s:
        yield FakeAsyncSession(s)


# --- one request per search, cache, budget -------------------------------------------------------

def test_one_search_makes_one_request_with_all_names(store):
    gw = FakeGateway([article("Tesla signs battery deal with Panasonic", "https://x.com/1")])
    items = fetch(["TSLA", "PCRFY", "F"], gw, store)
    assert len(gw.calls) == 1
    q = gw.calls[0].boolean_terms
    assert q == '"Tesla" OR "Panasonic" OR "Ford Motor"'
    assert gw.calls[0].from_time == NOW - timedelta(days=7)
    assert [i.entities for i in items] == [["TSLA", "PCRFY"]]


def test_stored_result_is_reused_within_ttl_then_refreshed(store):
    gw = FakeGateway([article("Tesla unveils a new car", "https://x.com/1")])
    fetch(["TSLA"], gw, store)
    again = fetch(["TSLA"], gw, ne.NewsStore(store.path), now=NOW + timedelta(hours=5))
    assert len(gw.calls) == 1 and [i.title for i in again] == ["Tesla unveils a new car"]
    fetch(["TSLA"], gw, ne.NewsStore(store.path), now=NOW + timedelta(hours=7))
    assert len(gw.calls) == 2


def test_only_stale_companies_are_queried(store):
    gw = FakeGateway([])
    fetch(["TSLA"], gw, store)
    fetch(["TSLA", "F"], gw, store, now=NOW + timedelta(hours=1))
    assert [c.boolean_terms for c in gw.calls] == ['"Tesla"', '"Ford Motor"']


def test_daily_budget_stops_requests_and_serves_stale_result(store):
    cfg = Config(newsapi_key="k", graph_news_daily_budget=2, graph_news_ttl_hours=1)
    gw = FakeGateway([article("Tesla recalls cars", "https://x.com/1")])
    for h in range(4):
        items = fetch(["TSLA"], gw, store, now=NOW + timedelta(hours=2 * h), cfg=cfg)
    assert len(gw.calls) == 2
    assert [i.title for i in items] == ["Tesla recalls cars"]  # stale copy, not nothing
    assert store.requests_today(NOW) == 2
    fetch(["TSLA"], gw, store, now=NOW + timedelta(days=1), cfg=cfg)  # a new UTC day resets it
    assert len(gw.calls) == 3


def test_newsapi_error_falls_back_without_raising(store):
    gw = FakeGateway(error=NewsApiError("rateLimited", "slow down", True))
    assert fetch(["TSLA"], gw, store) == []
    assert store.cached("TSLA") is None  # a failure is not stored as an empty result


def test_fake_mode_calls_nothing(store):
    gw = FakeGateway([article("Tesla", "https://x.com/1")])
    assert fetch(["TSLA"], gw, store, cfg=Config(graph_fake=1)) == []
    assert gw.calls == []


def test_query_splits_only_past_the_500_character_limit():
    short = [f"Company {i}" for i in range(13)]
    assert len(ne.build_queries(short)) == 1
    long = [f"A rather long company name number {i:02d}" for i in range(20)]
    queries = ne.build_queries(long)
    assert len(queries) > 1 and all(len(q) <= 500 for q in queries)
    assert sum(q.count('"') for q in queries) == 2 * len(long)


def test_old_articles_and_untagged_articles_are_dropped(store):
    gw = FakeGateway([
        article("Tesla launches a robot", "https://x.com/old", hours_ago=24 * 8),
        article("Unrelated story", "https://x.com/none"),
    ])
    assert fetch(["TSLA"], gw, store) == []


def test_short_ticker_does_not_tag_ordinary_words(store):
    gw = FakeGateway([
        article("Tesla signs a deal", "https://x.com/1"),
        article("Agilent Technologies wins an order", "https://x.com/2"),
    ])
    items = fetch(["TSLA", "A"], gw, store)
    assert {i.url: i.entities for i in items} == {"https://x.com/1": ["TSLA"], "https://x.com/2": ["A"]}


# --- dedup ---------------------------------------------------------------------------------------

def test_same_story_from_two_outlets_becomes_one_event(store, session, jev):
    gw = FakeGateway([
        article("Tesla recalls 100,000 Model Y SUVs - Reuters", "https://reuters.com/a", hours_ago=30),
        article("Tesla recalls 100,000 Model Y SUVs | CNBC", "https://cnbc.com/b", hours_ago=29),
        article("Tesla recalls 100,000 Model Y SUVs", "https://www.reuters.com/a/?utm=x", hours_ago=28),
    ])
    events = asyncio.run(ne.refresh_news_events(session, ["TSLA"], cfg=CFG, store=store, gateway=gw,
                                                aliases=DIRECTORY.aliases_for, now=NOW))
    assert len(gw.calls) == 1
    assert [(e.event_type, e.url) for e in events] == [("recall", "https://reuters.com/a")]
    rows = session.sync.execute(select(GraphEvent)).scalars().all()
    assert [(r.entity_symbol, r.source, r.event_type, r.url) for r in rows] == [
        ("TSLA", "news", "recall", "https://reuters.com/a")]


def test_dedupe_merges_company_tags():
    items = ne.dedupe([
        ne.ContentItem(id="u1", title="Ford buys a supplier", url="https://a.com/1", published_at=NOW,
                       entities=["F"]),
        ne.ContentItem(id="u2", title="FORD BUYS A SUPPLIER!", url="https://b.com/2",
                       published_at=NOW + timedelta(hours=1), entities=["PCRFY"]),
    ])
    assert len(items) == 1 and items[0].entities == ["F", "PCRFY"] and items[0].url == "https://a.com/1"


# --- classification ------------------------------------------------------------------------------

def test_opinion_piece_produces_no_event(store, session, jev):
    gw = FakeGateway([article("Opinion: Why Tesla is overrated", "https://x.com/op")])
    events = asyncio.run(ne.refresh_news_events(session, ["TSLA"], cfg=CFG, store=store, gateway=gw,
                                                aliases=DIRECTORY.aliases_for, now=NOW))
    assert events == []
    assert session.sync.execute(select(GraphEvent)).scalars().all() == []


HEADLINES = [  # (headline, the correct type)
    ("Tesla unveils cheaper Model 2 at investor event", "product_launch"),
    ("Panasonic launches new 4680 battery cell line", "product_launch"),
    ("Ford wins $2 billion Pentagon vehicle contract", "contract"),
    ("Panasonic signs supply deal with Tesla for Nevada plant", "contract"),
    ("Tesla beats third-quarter earnings estimates", "earnings_surprise"),
    ("Ford misses profit forecasts as warranty costs climb", "earnings_surprise"),
    ("Ford recalls 240,000 Explorers over brake issue", "recall"),
    ("Panasonic agrees to acquire Blue Yonder for $7 billion", "acquisition"),
    ("Opinion: Tesla's valuation makes no sense", None),
    ("5 EV stocks to watch this week", None),
]


def test_ten_headlines_at_least_eight_correct(jev):
    correct = 0
    for title, expected in HEADLINES:
        item = ne.ContentItem(id=title, title=title, url=f"https://x.com/{len(title)}", published_at=NOW)
        correct += ne.classify_event(item) == expected
    assert correct >= 8, correct


def test_classify_event_maps_unknown_and_none_labels_to_none():
    def stub(label):
        return lambda *a: type("R", (), {"label": label})()
    item = ne.ContentItem(id="u", title="t", url="https://x.com/u", published_at=NOW)
    assert ne.classify_event(item, classifier=stub("none")) is None
    assert ne.classify_event(item, classifier=stub("odds_move")) is None
    assert ne.classify_event(item, classifier=stub("recall")) == "recall"


def test_jev_labels_are_reused_and_failures_retried(store):
    calls = []

    def classifier(title, text, spec):
        calls.append(title)
        if title.startswith("Broken"):
            raise JevError("down")
        return type("R", (), {"label": "contract"})()

    items = [ne.ContentItem(id=t, title=t, url=f"https://x.com/{t}", published_at=NOW, entities=["F"])
             for t in ("Ford deal", "Broken story")]
    first = asyncio.run(ne.classify_items(items, store=store, classifier=classifier, now=NOW))
    second = asyncio.run(ne.classify_items(items, store=store, classifier=classifier, now=NOW))
    assert [e.title for e in first] == [e.title for e in second] == ["Ford deal"]
    assert calls == ["Ford deal", "Broken story", "Broken story"]


# --- saving --------------------------------------------------------------------------------------

def test_save_writes_one_row_per_company_and_skips_existing(session):
    event = ne.NewsEvent(symbols=["TSLA", "PCRFY"], event_type="contract", title="Tesla signs Panasonic deal",
                         url="https://x.com/1", occurred_at=NOW)
    first = asyncio.run(ne.save_news_events(session, [event]))
    second = asyncio.run(ne.save_news_events(session, [event]))
    assert len(first) == 2 and second == []
    rows = session.sync.execute(select(GraphEvent).order_by(GraphEvent.entity_symbol)).scalars().all()
    assert [(r.entity_symbol, r.source, r.event_type, r.alert_id) for r in rows] == [
        ("PCRFY", "news", "contract", None), ("TSLA", "news", "contract", None)]


def test_store_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "news_cache.json"
    path.write_text("{not json")
    s = ne.NewsStore(path)
    assert s.requests_today(NOW) == 0
    s.record_request(NOW)
    s.save()
    assert ne.NewsStore(path).requests_today(NOW) == 1


def test_search_terms_fall_back_to_symbol_for_non_us_companies():
    assert ne.search_terms([EntityAlias(symbol="Samsung Electronics", aliases=[]),
                            EntityAlias(symbol="TSLA", aliases=["Tesla, Inc.", "Tesla"])]) == [
        "Samsung Electronics", "Tesla"]


# --- classification with GRAPH_LLM_PROVIDER=anthropic (no OpenRouter, no Jev) --------------------

class FakeComplete:
    """Stands in for llm.complete: answers with keyword_label(title + text) and records the prompts."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, system, user, response_model, model=None):
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return response_model(label=keyword_label(user.split("Title: ", 1)[1]))


@pytest.fixture
def anthropic_model(monkeypatch):
    from company_graph import llm

    monkeypatch.setenv("GRAPH_LLM_PROVIDER", "anthropic")
    fake = FakeComplete()
    monkeypatch.setattr(llm, "complete", fake)
    return fake


def test_anthropic_provider_types_events_with_the_model_not_jev(anthropic_model):
    # conftest makes any Jev call fail, so a pass means Jev was not used.
    correct = 0
    for title, expected in HEADLINES:
        item = ne.ContentItem(id=title, title=title, url=f"https://x.com/{len(title)}", published_at=NOW)
        correct += ne.classify_event(item) == expected
    assert correct >= 8, correct
    system, user = anthropic_model.calls[0]
    for label, description in ne.EVENT_LABELS.items():
        assert f"- {label}: {description}" in system
    assert "Title: Tesla unveils cheaper Model 2" in user


def test_anthropic_reply_schema_allows_only_the_event_labels():
    assert set(ne.EventLabel.model_json_schema()["properties"]["label"]["enum"]) == set(NEWS_EVENT_TYPES) | {"none"}
    with pytest.raises(ValueError):
        ne.EventLabel(label="odds_move")


def test_anthropic_none_label_maps_to_none(monkeypatch):
    from company_graph import llm

    monkeypatch.setattr(llm, "complete", lambda s, u, m, model=None: m(label="none"))
    item = ne.ContentItem(id="u", title="5 EV stocks to watch", url="https://x.com/u", published_at=NOW)
    assert ne.classify_event(item, provider="anthropic") is None


def test_explicit_openrouter_provider_still_uses_jev(monkeypatch):
    from company_graph import llm

    def no_model(*args, **kwargs):
        raise AssertionError("the model must not be used with the openrouter provider")

    class Result:
        label = "recall"

    monkeypatch.setenv("GRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(llm, "complete", no_model)
    item = ne.ContentItem(id="u", title="t", url="https://x.com/u", published_at=NOW)
    assert ne.classify_event(item, classifier=lambda *a: Result(), provider="openrouter") == "recall"


def test_refresh_with_anthropic_provider_saves_typed_events(store, session, anthropic_model):
    gw = FakeGateway([
        article("Tesla recalls 100,000 Model Y SUVs", "https://reuters.com/a"),
        article("Opinion: Why Tesla is overrated", "https://x.com/op"),
    ])
    cfg = Config(newsapi_key="test-key", graph_llm_provider="anthropic")
    events = asyncio.run(ne.refresh_news_events(session, ["TSLA"], cfg=cfg, store=store, gateway=gw,
                                                aliases=DIRECTORY.aliases_for, now=NOW))
    assert [(e.event_type, e.url) for e in events] == [("recall", "https://reuters.com/a")]
    assert len(anthropic_model.calls) == 2
    rows = session.sync.execute(select(GraphEvent)).scalars().all()
    assert [(r.entity_symbol, r.event_type) for r in rows] == [("TSLA", "recall")]


def test_model_failure_is_skipped_and_retried_next_time(store, monkeypatch):
    from company_graph import llm

    failing = FakeComplete(error=llm.LlmError("overloaded"))
    monkeypatch.setattr(llm, "complete", failing)
    items = [ne.ContentItem(id="r", title="Ford recalls Explorers", url="https://x.com/r", published_at=NOW,
                            entities=["F"])]
    assert asyncio.run(ne.classify_items(items, store=store, now=NOW, provider="anthropic")) == []
    assert store.label("https://x.com/r") == (False, None)

    working = FakeComplete()
    monkeypatch.setattr(llm, "complete", working)
    events = asyncio.run(ne.classify_items(items, store=store, now=NOW, provider="anthropic"))
    assert [e.event_type for e in events] == ["recall"] and len(working.calls) == 1
