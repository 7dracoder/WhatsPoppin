import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.classification import JevError
from company_graph import api
from company_graph import pair_news as pn
from company_graph.companies import Company
from company_graph.config import Config
from company_graph.db import create_tables
from tests.company_graph.test_api import DIRECTORY, SqliteDb, make_app, seed_links

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
CFG = Config(newsapi_key="k", x_bearer_token="t")
NVDA = Company(symbol="NVDA", name="NVIDIA CORP", cik=1045810)
NBIS = Company(symbol="NBIS", name="Nebius Group N.V.", cik=1513845)
NAMES = {"NVDA": "Nvidia", "NBIS": "Nebius"}


def item(title, url, source="news", hours_ago=10, by="Reuters"):
    return pn.PairItem(source=source, title=title, url=url, published_at=NOW - timedelta(hours=hours_ago), by=by)


class FakeSearch:
    def __init__(self, items=(), error=None):
        self.items, self.error, self.calls = list(items), error, []

    def __call__(self, cfg, query, *window):
        self.calls.append((query, window))
        if self.error:
            raise self.error
        return list(self.items)


class Result:
    def __init__(self, label, probability=0.9):
        self.label, self.probability = label, probability


class FakeJev:
    """Says "together" for titles containing any of `together`, "separate" otherwise."""

    def __init__(self, together=(), error=None):
        self.together, self.error, self.calls = together, error, []

    def __call__(self, title, text, spec):
        self.calls.append((title, spec))
        if self.error:
            raise self.error
        return Result(pn.TOGETHER if any(t in title for t in self.together) else "separate")


@pytest.fixture
def store(tmp_path):
    return pn.PairStore(tmp_path / "pair_cache.json")


def run(store, news=None, x=None, jev=None, cfg=CFG, now=NOW, a=NVDA, b=NBIS):
    return asyncio.run(pn.pair_news(a, b, cfg=cfg, store=store, names=lambda syms: NAMES,
                                    news_search=news or FakeSearch(), x_search=x or FakeSearch(),
                                    classifier=jev or FakeJev(), now=now))


# --- queries and Jev -----------------------------------------------------------------------------

def test_both_names_are_required_in_each_query(store):
    news, x = FakeSearch(), FakeSearch()
    run(store, news=news, x=x)
    assert news.calls[0][0] == '"Nvidia" AND "Nebius"'
    assert x.calls[0][0] == '"Nvidia" "Nebius" -is:retweet -is:reply lang:en'
    (news_start,) = news.calls[0][1]
    assert news_start == NOW - timedelta(days=pn.NEWS_DAYS)
    x_start, x_end = x.calls[0][1]
    assert NOW - x_start < timedelta(days=7) and x_end < NOW


def test_only_items_about_the_pair_together_are_kept_newest_first(store):
    news = FakeSearch([item("Nebius signs $17B Nvidia GPU deal", "https://n/1", hours_ago=30),
                       item("Top 10 AI stocks: Nvidia, Nebius, AMD", "https://n/2")])
    x = FakeSearch([item("@d on X: Nebius brings Nvidia Blackwell online", "https://x.com/d/status/1",
                         source="x", hours_ago=2, by="@d")])
    result = run(store, news=news, x=x, jev=FakeJev(together=("deal", "Blackwell")))
    assert [i.url for i in result.items] == ["https://x.com/d/status/1", "https://n/1"]
    assert result.failed == []


def test_jev_is_asked_with_both_names_and_the_article_text():
    class Seen(FakeJev):
        def __call__(self, title, text, spec):
            self.text = text
            return super().__call__(title, text, spec)

    jev = Seen(together=("deal",))
    news = item("Nebius signs Nvidia deal", "u").model_copy(update={"text": "Nebius will deploy Blackwell GPUs"})
    assert pn.is_together(news, "Nvidia", "Nebius", jev)
    assert jev.text == "Nebius will deploy Blackwell GPUs"
    _, spec = jev.calls[0]
    assert spec.question == "Is this text about Nvidia and Nebius together?"
    assert set(spec.labels) == {pn.TOGETHER, "separate"}


def test_low_probability_and_jev_failures_are_left_out(store):
    class Unsure:
        def __call__(self, title, text, spec):
            return Result(pn.TOGETHER, probability=0.5)

    news = FakeSearch([item("Nebius and Nvidia", "https://n/1")])
    assert run(store, news=news, jev=Unsure()).items == []
    store2 = pn.PairStore(store.path.with_name("other.json"))
    assert run(store2, news=news, jev=FakeJev(error=JevError("down"))).items == []


def test_dedupe_keeps_relevance_order():
    items = [item("Most relevant", "https://a/1", hours_ago=1), item("Less relevant", "https://a/2", hours_ago=50)]
    assert [i.url for i in pn.dedupe(items)] == ["https://a/1", "https://a/2"]


def test_copies_are_dropped_before_jev(store):
    news = FakeSearch([item("Nebius signs Nvidia deal - Reuters", "https://a/1", hours_ago=5),
                       item("Nebius signs Nvidia deal - CNBC", "https://b/2", hours_ago=4),
                       item("Nebius signs Nvidia deal", "https://a/1/", hours_ago=3)])
    jev = FakeJev(together=("deal",))
    result = run(store, news=news, jev=jev)
    assert [i.url for i in result.items] == ["https://a/1"] and len(jev.calls) == 1


# --- cache, budget, failures ---------------------------------------------------------------------

def test_result_is_cached_per_pair_in_either_order(store):
    news = FakeSearch([item("Nebius signs Nvidia deal", "https://n/1")])
    jev = FakeJev(together=("deal",))
    first = run(store, news=news, jev=jev)
    again = run(store, news=news, jev=jev, a=NBIS, b=NVDA, now=NOW + timedelta(hours=1))
    assert again == first and len(news.calls) == 1
    run(store, news=news, jev=jev, now=NOW + timedelta(hours=7))
    assert len(news.calls) == 2


def test_one_source_failing_still_returns_the_other(store):
    x = FakeSearch([item("@d on X: Nebius signs Nvidia deal", "https://x.com/d/status/1", source="x")])
    result = run(store, news=FakeSearch(error=RuntimeError("NEWSAPI_KEY is not set")), x=x,
                 jev=FakeJev(together=("deal",)))
    assert result.failed == ["news"] and len(result.items) == 1


def test_nothing_is_cached_when_every_source_fails(store):
    news = FakeSearch(error=RuntimeError("down"))
    x = FakeSearch(error=RuntimeError("down"))
    assert run(store, news=news, x=x).failed == ["news", "x"]
    run(store, news=news, x=x, now=NOW + timedelta(minutes=1))
    assert len(news.calls) == 2


def test_daily_budget_counts_each_source(store):
    cfg = Config(graph_pair_daily_budget=1, graph_pair_ttl_hours=0.001)
    news, x = FakeSearch(), FakeSearch()
    run(store, news=news, x=x, cfg=cfg)
    result = run(store, news=news, x=x, cfg=cfg, now=NOW + timedelta(hours=1))
    assert len(news.calls) == 1 and len(x.calls) == 1
    assert result.failed == ["news", "x"]


def test_fake_mode_calls_nothing(store):
    news = FakeSearch()
    assert run(store, news=news, cfg=Config(graph_fake=1)).items == []
    assert news.calls == []


def test_x_search_drops_stock_lists(monkeypatch):
    class Gateway:
        def __init__(self, bearer_token):
            pass

        def fetch_recent_search(self, query, start, end, max_pages, sort_order):
            return [
                {"id": "1", "text": "Nebius signs Nvidia deal", "created_at": "2026-10-10T10:00:00Z",
                 "_author_username": "d", "public_metrics": {}},
                {"id": "2", "text": "Watch $NVDA $NBIS $AMD $MU today", "created_at": "2026-10-10T10:00:00Z",
                 "_author_username": "e", "public_metrics": {}},
            ]

    import backend.ingestion.twitter_lookup as tl

    monkeypatch.setattr(tl, "TwitterApiGateway", Gateway)
    items = pn._x_search(CFG, "q", NOW - timedelta(days=1), NOW)
    assert [(i.url, i.by) for i in items] == [("https://x.com/d/status/1", "@d")]
    assert items[0].title == "@d on X: Nebius signs Nvidia deal"


# --- the route -----------------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'graph.db'}", connect_args={"check_same_thread": False})
    with eng.begin() as conn:
        create_tables(conn)
    with Session(eng) as s:
        seed_links(s)
        s.commit()
    yield SqliteDb(eng)
    eng.dispose()


class FakePairNews:
    def __init__(self):
        self.calls = []

    async def __call__(self, a, b, *, cfg):
        self.calls.append((a, b))
        return pn.PairResult(items=[item("Tesla expands NVIDIA supercomputer deal", "https://n/1")], failed=["x"])


def client(db, search, cfg=None):
    app = make_app(db=db, cfg=cfg)
    app.dependency_overrides[api.get_pair_news] = lambda: search
    return TestClient(app)


def test_route_returns_items_for_a_linked_pair(db):
    search = FakePairNews()
    with client(db, search) as c:
        r = c.get("/graph/TSLA/news/NVDA")
    assert r.status_code == 200
    body = r.json()
    assert body["company"]["symbol"] == "TSLA" and body["other"] == {"symbol": "NVDA", "name": "NVIDIA CORP"}
    assert body["items"][0]["url"] == "https://n/1" and body["items"][0]["published_at"].endswith("Z")
    assert body["failed"] == ["x"]
    (a, b), = search.calls
    assert a.symbol == "TSLA" and b.symbol == "NVDA"


def test_route_refuses_a_pair_that_is_not_linked(db):
    search = FakePairNews()
    with client(db, search) as c:
        r = c.get("/graph/TSLA/news/AAPL")
    assert r.status_code == 404 and search.calls == []


def test_route_in_fake_mode_returns_no_items(db):
    with client(db, FakePairNews(), cfg=Config(graph_fake=1)) as c:
        r = c.get("/graph/NVDA/news/TSM")
    assert r.status_code == 200 and r.json()["items"] == []


def test_route_does_not_shadow_the_graph_route(db):
    assert DIRECTORY.get("TSLA") is not None
    paths = [route.path for route in api.router.routes]
    assert "/graph/{ticker}" in paths and "/graph/{ticker}/news/{other}" in paths
