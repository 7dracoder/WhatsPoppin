from fastapi.testclient import TestClient

import backend.api.twitter as twitter_router
from backend.api.deps import get_twitter_gateway
from backend.classification import ClassificationResult, JevError
from backend.ingestion.twitter_lookup.models import TwitterApiError
from backend.llm import LlmError
from backend.main import app

SAMPLE_TWEET = {
    "id": "1234567890",
    "text": "Nvidia just announced a new AI chip",
    "created_at": "2026-10-10T14:32:00.000Z",
    "_author_username": "realDonaldTrump",
    "public_metrics": {"retweet_count": 1, "reply_count": 2, "like_count": 3, "quote_count": 4},
}


class FakeGateway:
    def __init__(self, tweets=None, error=None):
        self._tweets = tweets or []
        self._error = error
        self.calls = []  # (method, start, end)

    def fetch_user_timeline(self, username, start, end):
        self.calls.append(("fetch_user_timeline", start, end))
        if self._error:
            raise self._error
        return self._tweets

    def fetch_recent_search(self, query, start, end):
        self.calls.append(("fetch_recent_search", start, end))
        if self._error:
            raise self._error
        return self._tweets


def test_get_account_tweets_returns_content_items():
    app.dependency_overrides[get_twitter_gateway] = lambda: FakeGateway(tweets=[SAMPLE_TWEET])
    client = TestClient(app)
    try:
        resp = client.get("/twitter/account/realDonaldTrump")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["author"] == "realDonaldTrump"
    assert data[0]["engagement"]["likes"] == 3


def test_search_tweets_returns_content_items():
    app.dependency_overrides[get_twitter_gateway] = lambda: FakeGateway(tweets=[SAMPLE_TWEET])
    client = TestClient(app)
    try:
        resp = client.get("/twitter/search", params={"query": "#nvidia"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_search_tweets_clamps_an_old_start_to_x_search_window():
    gateway = FakeGateway(tweets=[SAMPLE_TWEET])
    app.dependency_overrides[get_twitter_gateway] = lambda: gateway
    client = TestClient(app)
    try:
        resp = client.get(
            "/twitter/search",
            params={"query": "#nvidia", "start": "2026-07-13T00:00:00Z", "end": "2026-10-11T00:00:00Z"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    [(_, start, end)] = gateway.calls
    assert (end - start) == twitter_router.SEARCH_LOOKBACK


def test_search_tweets_maps_non_retryable_error_to_400():
    error = TwitterApiError(code="400", message="bad query", retryable=False)
    app.dependency_overrides[get_twitter_gateway] = lambda: FakeGateway(error=error)
    client = TestClient(app)
    try:
        resp = client.get("/twitter/search", params={"query": "#nvidia"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 400


BULLISH_TWEET = {**SAMPLE_TWEET, "id": "1", "text": "BULLISH_TEST tweet"}
UNRELATED_TWEET = {**SAMPLE_TWEET, "id": "2", "text": "UNRELATED_TEST tweet"}


def _fake_classify(title, text, spec):
    """Stands in for backend.classification.classify in these tests —
    decides label from the tweet text (passed as `title`, per the
    title/text swap) so no real Jev call happens.
    """
    if "UNRELATED_TEST" in title:
        return ClassificationResult(mode="choice", label="unrelated", probability=0.9,
                                     probabilities={"bullish": 0.05, "bearish": 0.03, "neutral": 0.02,
                                                    "unrelated": 0.9}, confidence=0.9, raw={})
    return ClassificationResult(mode="choice", label="bullish", probability=0.8,
                                 probabilities={"bullish": 0.8, "bearish": 0.05, "neutral": 0.1,
                                                "unrelated": 0.05}, confidence=0.85, raw={})


def test_classify_requires_exactly_one_of_handle_or_query():
    app.dependency_overrides[get_twitter_gateway] = lambda: FakeGateway(tweets=[])
    client = TestClient(app)
    try:
        neither = client.get("/twitter/classify", params={"entity": "NVDA"})
        both = client.get(
            "/twitter/classify", params={"entity": "NVDA", "handle": "x", "query": "y"}
        )
    finally:
        app.dependency_overrides.clear()

    assert neither.status_code == 400
    assert both.status_code == 400


def test_classify_drops_unrelated_and_keeps_correlating_tweets(monkeypatch):
    monkeypatch.setattr(twitter_router, "classify", _fake_classify)
    app.dependency_overrides[get_twitter_gateway] = lambda: FakeGateway(
        tweets=[BULLISH_TWEET, UNRELATED_TWEET]
    )
    client = TestClient(app)
    try:
        resp = client.get(
            "/twitter/classify",
            params={"entity": "NVDA", "handle": "realDonaldTrump", "tags": "oil,shipping"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "1"
    assert data[0]["classification"]["label"] == "bullish"


def test_classify_falls_back_to_generic_categories_when_llm_unreachable(monkeypatch):
    monkeypatch.setattr(twitter_router, "classify", _fake_classify)

    def _raise_llm_error(**kwargs):
        raise LlmError("OPENROUTER not set")

    monkeypatch.setattr(twitter_router, "complete_structured", _raise_llm_error)
    app.dependency_overrides[get_twitter_gateway] = lambda: FakeGateway(tweets=[BULLISH_TWEET])
    client = TestClient(app)
    try:
        resp = client.get("/twitter/classify", params={"entity": "NVDA", "handle": "realDonaldTrump"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(resp.json()) == 1  # generic default categories still let classification proceed


def test_classify_passes_explicit_start_and_end_to_the_gateway(monkeypatch):
    monkeypatch.setattr(twitter_router, "classify", _fake_classify)
    gateway = FakeGateway(tweets=[BULLISH_TWEET])
    app.dependency_overrides[get_twitter_gateway] = lambda: gateway
    client = TestClient(app)
    try:
        resp = client.get(
            "/twitter/classify",
            params={"entity": "NVDA", "query": "nvidia",
                    "start": "2026-10-01T00:00:00Z", "end": "2026-10-08T00:00:00Z"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    [(_, start, end)] = gateway.calls
    assert start.isoformat() == "2026-10-01T00:00:00+00:00"
    assert end.isoformat() == "2026-10-08T00:00:00+00:00"


def test_classify_clamps_an_old_start_for_a_query_search(monkeypatch):
    monkeypatch.setattr(twitter_router, "classify", _fake_classify)
    gateway = FakeGateway(tweets=[BULLISH_TWEET])
    app.dependency_overrides[get_twitter_gateway] = lambda: gateway
    client = TestClient(app)
    try:
        resp = client.get(
            "/twitter/classify",
            params={"entity": "NVDA", "query": "nvidia",
                    "start": "2026-07-13T00:00:00Z", "end": "2026-10-11T00:00:00Z"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    [(_, start, end)] = gateway.calls
    assert (end - start) == twitter_router.SEARCH_LOOKBACK


def test_classify_defaults_to_the_usual_lookback_without_start_or_end(monkeypatch):
    monkeypatch.setattr(twitter_router, "classify", _fake_classify)
    gateway = FakeGateway(tweets=[BULLISH_TWEET])
    app.dependency_overrides[get_twitter_gateway] = lambda: gateway
    client = TestClient(app)
    try:
        resp = client.get("/twitter/classify", params={"entity": "NVDA", "query": "nvidia"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    [(_, start, end)] = gateway.calls
    assert (end - start) == twitter_router.SEARCH_LOOKBACK


def test_classify_drops_post_on_jev_error(monkeypatch):
    def _raise_jev_error(title, text, spec):
        raise JevError("Jev call failed")

    monkeypatch.setattr(twitter_router, "classify", _raise_jev_error)
    app.dependency_overrides[get_twitter_gateway] = lambda: FakeGateway(tweets=[BULLISH_TWEET])
    client = TestClient(app)
    try:
        resp = client.get(
            "/twitter/classify", params={"entity": "NVDA", "handle": "realDonaldTrump", "tags": "oil"}
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []
