import json
from datetime import datetime, timezone
from pathlib import Path

from backend.ingestion.twitter_lookup.client import TwitterApiGateway
from backend.ingestion.twitter_lookup.entity_match import EntityAlias
from backend.ingestion.twitter_lookup.service import lookup_account, lookup_keyword

FIXTURES = Path(__file__).parent / "fixtures"
START = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code: int, json_data: dict):
        self.status_code = status_code
        self._json = json_data
        self.text = ""

    def json(self):
        return self._json


class FakeHttpxClient:
    def __init__(self, responses: dict[str, FakeResponse]):
        self._responses = responses

    def get(self, path, params=None):
        return self._responses[path]


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_lookup_keyword_end_to_end_tags_entities():
    fake = FakeHttpxClient({
        "/tweets/search/recent": FakeResponse(200, _load("sample_tweets_response.json")),
    })
    gateway = TwitterApiGateway(bearer_token="dummy", http_client=fake)
    entities = [EntityAlias(symbol="NVDA", aliases=["Nvidia"])]

    items = lookup_keyword('"strait of hormuz" OR #nvidia', START, END, gateway, entities)

    # The fixture's second tweet has empty text and is dropped by normalize_batch,
    # so only the Nvidia tweet survives.
    assert len(items) == 1
    assert items[0].entities == ["NVDA"]
    assert items[0].author == "realDonaldTrump"
    assert items[0].engagement.likes == 500


def test_lookup_keyword_returns_empty_entities_when_no_match():
    fake = FakeHttpxClient({
        "/tweets/search/recent": FakeResponse(200, _load("sample_tweets_response.json")),
    })
    gateway = TwitterApiGateway(bearer_token="dummy", http_client=fake)
    entities = [EntityAlias(symbol="TSLA", aliases=["Tesla"])]

    items = lookup_keyword("#nvidia", START, END, gateway, entities)

    assert items[0].entities == []


def test_lookup_account_end_to_end():
    fake = FakeHttpxClient({
        "/users/by/username/realDonaldTrump": FakeResponse(200, _load("sample_user_lookup_response.json")),
        "/users/999/tweets": FakeResponse(200, _load("sample_tweets_response.json")),
    })
    gateway = TwitterApiGateway(bearer_token="dummy", http_client=fake)
    entities = [EntityAlias(symbol="NVDA", aliases=["Nvidia"])]

    items = lookup_account("realDonaldTrump", START, END, gateway, entities)

    assert len(items) == 1
    assert items[0].author == "realDonaldTrump"
    assert items[0].entities == ["NVDA"]
