import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.ingestion.twitter_lookup.client import TwitterApiGateway
from backend.ingestion.twitter_lookup.models import TwitterApiError

FIXTURES = Path(__file__).parent / "fixtures"
START = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)


class FakeResponse:
    """Stands in for httpx.Response — no real HTTP calls in tests."""

    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeHttpxClient:
    """Stands in for httpx.Client. `responses` maps an exact request path to
    a FakeResponse, or a list of FakeResponses consumed in order (for
    pagination) — the last one in a list repeats if called again.
    """

    def __init__(self, responses: dict[str, FakeResponse | list[FakeResponse]]):
        self._responses = {k: (v if isinstance(v, list) else [v]) for k, v in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        queue = self._responses.get(path)
        if not queue:
            raise AssertionError(f"no fake response configured for GET {path}")
        return queue.pop(0) if len(queue) > 1 else queue[0]


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_fetch_recent_search_returns_tweets_with_resolved_authors():
    fake = FakeHttpxClient({
        "/tweets/search/recent": FakeResponse(200, _load("sample_tweets_response.json")),
    })
    gateway = TwitterApiGateway(bearer_token="dummy", http_client=fake)

    tweets = gateway.fetch_recent_search('"strait of hormuz"', START, END)

    assert len(tweets) == 2
    assert tweets[0]["_author_username"] == "realDonaldTrump"
    # query was passed through
    assert fake.calls[0][1]["query"] == '"strait of hormuz"'


def test_fetch_user_timeline_resolves_user_id_then_fetches_tweets():
    fake = FakeHttpxClient({
        "/users/by/username/realDonaldTrump": FakeResponse(200, _load("sample_user_lookup_response.json")),
        "/users/999/tweets": FakeResponse(200, _load("sample_tweets_response.json")),
    })
    gateway = TwitterApiGateway(bearer_token="dummy", http_client=fake)

    tweets = gateway.fetch_user_timeline("@realDonaldTrump", START, END)

    assert len(tweets) == 2
    assert tweets[0]["_author_username"] == "realDonaldTrump"


def test_fetch_user_timeline_raises_not_found_when_no_such_account():
    fake = FakeHttpxClient({
        "/users/by/username/nosuchaccount": FakeResponse(200, {"errors": [{"title": "Not Found Error"}]}),
    })
    gateway = TwitterApiGateway(bearer_token="dummy", http_client=fake)

    with pytest.raises(TwitterApiError) as exc_info:
        gateway.fetch_user_timeline("nosuchaccount", START, END)

    assert exc_info.value.code == "not_found"
    assert exc_info.value.retryable is False


def test_fetch_recent_search_raises_retryable_on_429():
    fake = FakeHttpxClient({
        "/tweets/search/recent": FakeResponse(429, {"title": "Too Many Requests"}),
    })
    gateway = TwitterApiGateway(bearer_token="dummy", http_client=fake)

    with pytest.raises(TwitterApiError) as exc_info:
        gateway.fetch_recent_search("#nvidia", START, END)

    assert exc_info.value.code == "429"
    assert exc_info.value.retryable is True


def test_fetch_recent_search_raises_non_retryable_on_400():
    fake = FakeHttpxClient({
        "/tweets/search/recent": FakeResponse(400, {"title": "Invalid Request"}),
    })
    gateway = TwitterApiGateway(bearer_token="dummy", http_client=fake)

    with pytest.raises(TwitterApiError) as exc_info:
        gateway.fetch_recent_search("#nvidia", START, END)

    assert exc_info.value.code == "400"
    assert exc_info.value.retryable is False


def test_fetch_user_timeline_follows_pagination():
    fake = FakeHttpxClient({
        "/users/by/username/realDonaldTrump": FakeResponse(200, _load("sample_user_lookup_response.json")),
        "/users/999/tweets": [
            FakeResponse(200, _load("sample_tweets_page1.json")),
            FakeResponse(200, _load("sample_tweets_page2.json")),
        ],
    })
    gateway = TwitterApiGateway(bearer_token="dummy", http_client=fake)

    tweets = gateway.fetch_user_timeline("realDonaldTrump", START, END)

    assert [t["id"] for t in tweets] == ["1111111111", "2222222222"]
    timeline_calls = [c for c in fake.calls if c[0] == "/users/999/tweets"]
    assert len(timeline_calls) == 2
    assert "pagination_token" not in timeline_calls[0][1]
    assert timeline_calls[1][1]["pagination_token"] == "page2token"


def test_fetch_recent_search_follows_pagination():
    fake = FakeHttpxClient({
        "/tweets/search/recent": [
            FakeResponse(200, _load("sample_tweets_page1.json")),
            FakeResponse(200, _load("sample_tweets_page2.json")),
        ],
    })
    gateway = TwitterApiGateway(bearer_token="dummy", http_client=fake)

    tweets = gateway.fetch_recent_search("#nvidia", START, END)

    assert [t["id"] for t in tweets] == ["1111111111", "2222222222"]
    assert fake.calls[1][1]["next_token"] == "page2token"
