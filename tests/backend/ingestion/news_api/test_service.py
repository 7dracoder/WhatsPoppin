import json
from pathlib import Path

from backend.entities import EntityAlias
from backend.ingestion.news_api.client import NewsApiGateway
from backend.ingestion.news_api.models import NewsQueryFilters
from backend.ingestion.news_api.service import poll_news

FIXTURES = Path(__file__).parent / "fixtures"


class FakeNewsApiClient:
    def __init__(self, response):
        self._response = response

    def get_everything(self, **kwargs):
        return self._response


def test_poll_news_end_to_end_tags_entities():
    response = json.loads((FIXTURES / "sample_everything_response.json").read_text())
    gateway = NewsApiGateway(api_key="dummy", http_client=FakeNewsApiClient(response))
    entities = [EntityAlias(symbol="NVDA", aliases=["Nvidia"])]

    items = poll_news(
        filters=NewsQueryFilters(keyword_query="nvidia"),
        gateway=gateway,
        entities=entities,
    )

    # The second fixture article is malformed (no url) and is dropped by
    # normalize_batch, so only the valid Nvidia article survives.
    assert len(items) == 1
    assert items[0].entities == ["NVDA"]
    assert items[0].title == "Nvidia unveils new AI chip as NVDA shares climb"


def test_poll_news_returns_empty_entities_when_no_match():
    response = json.loads((FIXTURES / "sample_everything_response.json").read_text())
    gateway = NewsApiGateway(api_key="dummy", http_client=FakeNewsApiClient(response))
    entities = [EntityAlias(symbol="TSLA", aliases=["Tesla"])]

    items = poll_news(
        filters=NewsQueryFilters(keyword_query="nvidia"),
        gateway=gateway,
        entities=entities,
    )

    assert items[0].entities == []
