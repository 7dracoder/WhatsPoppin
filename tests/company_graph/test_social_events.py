import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.classification import JevError
from backend.entities import EntityAlias
from backend.ingestion.twitter_lookup import ContentItem, Engagement, TwitterApiError
from backend.ingestion.twitter_lookup.client import TwitterApiGateway
from backend.models.graph_event import GraphEvent
from company_graph import highlights as hl
from company_graph import social_events as se
from company_graph.config import Config
from company_graph.db import create_tables

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
CFG = Config(x_bearer_token="test-token", graph_social_min_likes=5)

ENTITIES = {
    "TSLA": EntityAlias(symbol="TSLA", aliases=["Tesla, Inc.", "Tesla"]),
    "F": EntityAlias(symbol="F", aliases=["Ford Motor Co", "Ford"]),
    "A": EntityAlias(symbol="A", aliases=["AGILENT TECHNOLOGIES, INC.", "Agilent"]),
    "Panasonic Holdings": EntityAlias(symbol="Panasonic Holdings", aliases=[]),
}


def aliases(symbols):
    return [ENTITIES[s] for s in symbols]


def post(text, id="1", likes=50, hours_ago=5, author="newsdesk"):
    return ContentItem(id=id, author=author, text=text, url=f"https://x.com/{author}/status/{id}",
                       published_at=NOW - timedelta(hours=hours_ago), engagement=Engagement(likes=likes))


class FakeSearch:
    """Stands in for one X recent-search request."""

    def __init__(self, posts=(), error=None):
        self.posts, self.error, self.queries = list(posts), error, []

    def __call__(self, gateway, query, start, end):
        self.queries.append((query, start, end))
        if self.error:
            raise self.error
        return list(self.posts)


class Result:
    def __init__(self, label, probability=0.9):
        self.label, self.probability = label, probability


class FakeJev:
    def __init__(self, labels=None, default="none", error=None):
        self.labels, self.default, self.error, self.calls = labels or {}, default, error, []

    def __call__(self, title, text, spec):
        self.calls.append((title, text, spec))
        if self.error:
            raise self.error
        for needle, label in self.labels.items():
            if needle in title:
                return Result(*label) if isinstance(label, tuple) else Result(label)
        return Result(self.default)


@pytest.fixture
def store(tmp_path):
    return se.social_store(tmp_path / "social_cache.json")


class FakeAsyncSession:
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


def fetch(symbols, store, search, cfg=CFG, now=NOW):
    return asyncio.run(se.fetch_social_items(symbols, cfg=cfg, store=store, gateway=object(), search=search,
                                             aliases=aliases, now=now))


# --- queries, cache and budget -------------------------------------------------------------------

def test_one_search_names_every_company_and_leaves_out_retweets(store):
    search = FakeSearch([post("Tesla opens a plant")])
    fetch(["TSLA", "F"], store, search)
    assert len(search.queries) == 1
    query, start, end = search.queries[0]
    assert query == '("Tesla" OR "Ford") -is:retweet -is:reply lang:en'
    assert NOW - start < timedelta(days=7) and end < NOW


def test_queries_split_only_past_the_512_character_limit():
    names = [f"Company number {i:02d}" for i in range(30)]
    queries = se.build_queries(names)
    assert len(queries) == 2 and all(len(q) <= se.X_MAX_QUERY_CHARS for q in queries)
    assert se.build_queries(names[:5]) == [
        "(" + " OR ".join(f'"{n}"' for n in names[:5]) + ") " + se.QUERY_FILTERS]


def test_stored_result_is_reused_within_ttl(store):
    search = FakeSearch([post("Tesla opens a plant")])
    fetch(["TSLA"], store, search)
    fetch(["TSLA"], store, search, now=NOW + timedelta(hours=1))
    assert len(search.queries) == 1
    fetch(["TSLA"], store, search, now=NOW + timedelta(hours=7))
    assert len(search.queries) == 2


def test_daily_budget_stops_requests(store):
    search = FakeSearch([post("Tesla opens a plant")])
    cfg = Config(x_bearer_token="t", graph_social_daily_budget=1, graph_social_ttl_hours=0.001)
    fetch(["TSLA"], store, search, cfg=cfg)
    fetch(["TSLA"], store, search, cfg=cfg, now=NOW + timedelta(hours=1))
    assert len(search.queries) == 1


def test_x_error_falls_back_without_raising(store):
    search = FakeSearch(error=TwitterApiError(code="429", message="Too Many Requests", retryable=True))
    assert fetch(["TSLA"], store, search) == []


def test_fake_mode_calls_nothing(store):
    search = FakeSearch([post("Tesla opens a plant")])
    assert fetch(["TSLA"], store, search, cfg=Config(graph_fake=1)) == []
    assert search.queries == []


def test_search_uses_one_page_in_relevancy_order():
    class Gateway:
        def fetch_recent_search(self, query, start, end, max_pages, sort_order):
            self.args = (max_pages, sort_order)
            return [{"id": "9", "text": "Tesla recalls cars", "created_at": "2026-10-10T10:00:00Z",
                     "_author_username": "newsdesk", "public_metrics": {"like_count": 7}}]

    gw = Gateway()
    items = se._search(gw, "q", NOW - timedelta(days=1), NOW)
    assert gw.args == (1, "relevancy")
    assert items[0].url == "https://x.com/newsdesk/status/9" and items[0].engagement.likes == 7


def test_gateway_page_cap_and_sort_order_reach_x():
    class Http:
        def __init__(self):
            self.calls = []

        def get(self, path, params=None):
            self.calls.append(params)

            class R:
                status_code = 200

                def json(self_inner):
                    return {"data": [], "meta": {"next_token": "more"}}

            return R()

    http = Http()
    TwitterApiGateway("t", http_client=http).fetch_recent_search("q", NOW - timedelta(days=1), NOW,
                                                                  max_pages=1, sort_order="relevancy")
    assert len(http.calls) == 1 and http.calls[0]["sort_order"] == "relevancy"


# --- tagging and filtering -----------------------------------------------------------------------

def test_tags_aliases_cashtags_and_capital_tickers_only():
    ents = aliases(["TSLA", "F", "A", "Panasonic Holdings"])
    assert se.tag_post("tesla delivers record cars", ents) == ["TSLA"]
    assert se.tag_post("TSLA up today", ents) == ["TSLA"]
    assert se.tag_post("tsla up today", ents) == []
    assert se.tag_post("$F and $tsla both moving", ents) == ["TSLA", "F"]
    assert se.tag_post("A great day. F this.", ents) == []  # one-letter tickers need a cashtag
    assert se.tag_post("panasonic holdings opens a plant", ents) == ["Panasonic Holdings"]


def test_low_like_untagged_old_and_copied_posts_are_dropped(store):
    search = FakeSearch([
        post("Tesla opens a plant in Texas https://t.co/a", id="1", likes=100),
        post("Tesla opens a plant in Texas https://t.co/b", id="2", likes=40),  # copy
        post("Tesla opens a plant", id="3", likes=2),  # too few likes
        post("Nothing about any company", id="4"),
        post("Tesla recall widens", id="5", hours_ago=24 * 8),  # older than the window
    ])
    items = fetch(["TSLA"], store, search)
    assert [i.id for i in items] == ["1"]
    assert items[0].entities == ["TSLA"]


def test_keep_posts_caps_and_sorts_by_likes():
    items = [post(f"Tesla news {i}", id=str(i), likes=i).model_copy(update={"entities": ["TSLA"]})
             for i in range(10, 70)]
    kept = se.keep_posts(items, min_likes=5, limit=3)
    assert [i.likes for i in (k.engagement for k in kept)] == [69, 68, 67]


# --- Jev -----------------------------------------------------------------------------------------

def test_jev_decides_fact_or_ignore():
    jev = FakeJev({"recalls": "recall", "moon": "none", "unsure": ("contract", 0.6)})
    assert se.classify_post(post("Tesla recalls 10,000 cars"), "Tesla", jev) == "recall"
    assert se.classify_post(post("TSLA to the moon"), "Tesla", jev) is None
    assert se.classify_post(post("unsure about a Tesla deal"), "Tesla", jev) is None  # low probability
    title, text, spec = jev.calls[0]
    assert title == "Tesla recalls 10,000 cars" and text is None
    assert "Tesla itself" in spec.question and set(spec.labels) == set(se.POST_LABELS)


def test_jev_is_asked_once_per_company_a_post_names():
    item = post("Tesla signs a battery deal with Ford").model_copy(update={"entities": ["TSLA", "F"]})
    jev = FakeJev({"battery": "contract"})
    events = asyncio.run(se.classify_posts([item], names={"TSLA": "Tesla", "F": "Ford"}, classifier=jev, now=NOW))
    assert [c[2].question.split(" done by or to ")[1] for c in jev.calls] == ["Tesla itself?", "Ford itself?"]
    assert [(e.symbols, e.event_type) for e in events] == [(["TSLA"], "contract"), (["F"], "contract")]


def test_stock_lists_are_dropped():
    many = post("Top picks: Tesla, Ford, Agilent").model_copy(update={"entities": ["TSLA", "F", "A"]})
    cashtags = post("Watching $TSLA $NVDA $AMD $MU today").model_copy(update={"entities": ["TSLA"]})
    one = post("Tesla opens a plant").model_copy(update={"entities": ["TSLA"]})
    assert se.keep_posts([many, cashtags, one], min_likes=0) == [one]


def test_labels_are_reused_and_failures_retried(store):
    items = [post("Tesla recalls cars").model_copy(update={"entities": ["TSLA"]})]
    failing = FakeJev(error=JevError("down"))
    assert asyncio.run(se.classify_posts(items, store=store, classifier=failing, now=NOW)) == []
    jev = FakeJev({"recalls": "recall"})
    events = asyncio.run(se.classify_posts(items, store=store, classifier=jev, now=NOW))
    asyncio.run(se.classify_posts(items, store=store, classifier=jev, now=NOW))
    assert len(jev.calls) == 1 and [e.event_type for e in events] == ["recall"]


def test_refresh_saves_social_rows_with_the_post_as_title(store, session):
    search = FakeSearch([post("Tesla recalls 10,000 cars over a brake fault", id="7"),
                         post("TSLA to the moon", id="8")])
    jev = FakeJev({"recalls": "recall"})
    events = asyncio.run(se.refresh_social_events(session, ["TSLA"], cfg=CFG, store=store, gateway=object(),
                                                  search=search, aliases=aliases, classifier=jev, now=NOW))
    assert len(events) == 1
    rows = session.sync.execute(select(GraphEvent)).scalars().all()
    assert [(r.entity_symbol, r.source, r.event_type, r.url) for r in rows] == [
        ("TSLA", "social", "recall", "https://x.com/newsdesk/status/7")]
    assert rows[0].title == "@newsdesk on X: Tesla recalls 10,000 cars over a brake fault"


def test_post_title_is_capped():
    assert len(se.post_title(post("Tesla " * 100))) == se.TITLE_CHARS


# --- the highlight run ---------------------------------------------------------------------------

def test_highlight_social_step_is_left_out_without_a_key(monkeypatch):
    async def must_not_run(*a, **k):
        raise AssertionError("no X_BEARER_TOKEN: refresh_social_events must not run")

    monkeypatch.setattr(se, "refresh_social_events", must_not_run)
    assert asyncio.run(hl._default_social(None, ["TSLA"], cfg=Config())) == []
