from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.deps import get_news_gateway, get_news_store
from backend.api.evidence import router
from backend.evidence.news_cache import CachedNews, bucket, is_fresh
from backend.ingestion.news_api import ContentItem, NewsApiError

app = FastAPI()
app.include_router(router)

ARTICLE = {
    "source": {"id": None, "name": "Wire"},
    "title": "Nvidia unveils new chip",
    "description": "d",
    "content": "Nvidia announced...",
    "url": "https://example.com/a",
    "publishedAt": "2026-10-01T12:00:00Z",
}


class FakeGateway:
    def __init__(self, articles=None, error=None):
        self.articles, self.error, self.filters = articles or [], error, None

    def fetch_raw(self, filters):
        self.filters = filters
        if self.error:
            raise self.error
        return self.articles


class FakeStore:
    """In-memory NewsStore keyed like the real one; `fail` makes every call raise."""

    def __init__(self, fail=False):
        self.rows, self.fail, self.puts = {}, fail, 0

    async def get(self, ticker, start, end):
        if self.fail:
            raise RuntimeError("db down")
        return self.rows.get((ticker, start, end))

    async def put(self, ticker, start, end, items):
        if self.fail:
            raise RuntimeError("db down")
        self.puts += 1
        self.rows[(ticker, start, end)] = CachedNews(items, datetime.now(timezone.utc))

    async def latest(self, ticker):
        if self.fail:
            raise RuntimeError("db down")
        rows = [v for (t, _, _), v in self.rows.items() if t == ticker]
        return max(rows, key=lambda r: r.fetched_at) if rows else None


def get(gateway, store=None, **params):
    app.dependency_overrides[get_news_gateway] = lambda: gateway
    app.dependency_overrides[get_news_store] = lambda: store
    try:
        return TestClient(app).get("/evidence/news", params=params)
    finally:
        app.dependency_overrides.clear()


def test_news_returns_tagged_articles():
    gw = FakeGateway([ARTICLE])
    resp = get(gw, ticker="nvda")
    assert resp.status_code == 200
    item = resp.json()[0]
    assert item["source"] == "news" and item["entities"] == ["NVDA"]
    assert '"NVDA"' in gw.filters.boolean_terms and "Nvidia" in gw.filters.boolean_terms


def test_news_respects_start_end():
    gw = FakeGateway([ARTICLE])
    get(gw, ticker="NVDA", start="2026-07-01T00:00:00Z", end="2026-10-01T00:00:00Z")
    assert gw.filters.from_time == datetime(2026, 7, 1, tzinfo=timezone.utc)


def test_missing_credentials_serves_mock():
    resp = get(None, ticker="AAPL")
    items = resp.json()
    assert resp.status_code == 200 and len(items) > 0
    assert all(i["title"].startswith("[MOCK]") and i["entities"] == ["AAPL"] for i in items)


def test_mock_is_deterministic_for_fixed_window():
    params = dict(ticker="AAPL", start="2026-09-01T00:00:00Z", end="2026-10-01T00:00:00Z")
    assert get(None, **params).json() == get(None, **params).json()


def test_upstream_failure_falls_back_to_mock():
    gw = FakeGateway(error=NewsApiError("rateLimited", "slow down", True))
    resp = get(gw, ticker="NVDA")
    assert resp.status_code == 200 and resp.json()[0]["title"].startswith("[MOCK]")


WINDOW = dict(start="2026-09-01T06:00:00Z", end="2026-09-30T18:00:00Z")  # a past window: cache never expires


def test_bucket_rounds_to_whole_utc_days():
    start, end = bucket(datetime(2026, 9, 1, 6, tzinfo=timezone.utc), datetime(2026, 9, 30, 18, tzinfo=timezone.utc))
    assert (start, end) == (datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc))


def test_second_request_for_the_same_window_is_served_from_the_cache():
    gw, store = FakeGateway([ARTICLE]), FakeStore()
    first = get(gw, store, ticker="NVDA", **WINDOW).json()
    gw.articles = []  # NewsAPI would now answer differently
    second = get(gw, store, ticker="NVDA", start="2026-09-01T09:00:00Z", end="2026-09-30T20:00:00Z").json()
    assert first == second and len(first) == 1 and store.puts == 1


def test_upstream_failure_serves_the_last_cached_window_before_mock():
    store = FakeStore()
    get(FakeGateway([ARTICLE]), store, ticker="NVDA", **WINDOW)
    resp = get(FakeGateway(error=NewsApiError("rateLimited", "slow down", True)), store, ticker="NVDA",
               start="2026-10-01T00:00:00Z", end="2026-10-08T00:00:00Z")
    assert resp.status_code == 200 and resp.json()[0]["title"] == "Nvidia unveils new chip"


def test_a_broken_cache_is_ignored_not_an_error():
    resp = get(FakeGateway([ARTICLE]), FakeStore(fail=True), ticker="NVDA", **WINDOW)
    assert resp.status_code == 200 and resp.json()[0]["title"] == "Nvidia unveils new chip"


def test_live_window_goes_stale_after_refresh_interval():
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    item = ContentItem(id="u", title="t", url="u", published_at=now)
    fresh = CachedNews([item], now - timedelta(hours=1))
    old = CachedNews([item], now - timedelta(hours=7))
    today_end = datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert is_fresh(fresh, today_end, now) and not is_fresh(old, today_end, now)
    assert is_fresh(old, datetime(2026, 10, 1, tzinfo=timezone.utc), now)  # a past window never expires
