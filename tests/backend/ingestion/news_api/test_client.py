import json
from pathlib import Path

import pytest
from newsapi.newsapi_exception import NewsAPIException

from backend.ingestion.news_api.client import NewsApiGateway
from backend.ingestion.news_api.models import NewsApiError, NewsQueryFilters

FIXTURES = Path(__file__).parent / "fixtures"


class FakeNewsApiClient:
    """Stands in for newsapi.NewsApiClient — no real HTTP calls in tests."""

    def __init__(self, response=None, exception=None):
        self._response = response
        self._exception = exception

    def get_everything(self, **kwargs):
        if self._exception is not None:
            raise self._exception
        return self._response


def _load_response() -> dict:
    return json.loads((FIXTURES / "sample_everything_response.json").read_text())


def test_fetch_raw_returns_articles_on_ok_status():
    fake = FakeNewsApiClient(response=_load_response())
    gateway = NewsApiGateway(api_key="dummy", http_client=fake)

    articles = gateway.fetch_raw(NewsQueryFilters(keyword_query="nvidia"))

    assert len(articles) == 2
    assert articles[0]["title"] == "Nvidia unveils new AI chip as NVDA shares climb"


def test_fetch_raw_raises_on_non_ok_status():
    fake = FakeNewsApiClient(
        response={"status": "error", "code": "parametersMissing", "message": "q is required"}
    )
    gateway = NewsApiGateway(api_key="dummy", http_client=fake)

    with pytest.raises(NewsApiError) as exc_info:
        gateway.fetch_raw(NewsQueryFilters(keyword_query="nvidia"))

    assert exc_info.value.code == "parametersMissing"
    assert exc_info.value.retryable is False


def test_fetch_raw_translates_rate_limited_as_retryable():
    exc = NewsAPIException({"code": "rateLimited", "message": "too many requests"})
    fake = FakeNewsApiClient(exception=exc)
    gateway = NewsApiGateway(api_key="dummy", http_client=fake)

    with pytest.raises(NewsApiError) as exc_info:
        gateway.fetch_raw(NewsQueryFilters(keyword_query="nvidia"))

    assert exc_info.value.code == "rateLimited"
    assert exc_info.value.retryable is True


def test_fetch_raw_translates_invalid_api_key_as_not_retryable():
    exc = NewsAPIException({"code": "apiKeyInvalid", "message": "bad key"})
    fake = FakeNewsApiClient(exception=exc)
    gateway = NewsApiGateway(api_key="dummy", http_client=fake)

    with pytest.raises(NewsApiError) as exc_info:
        gateway.fetch_raw(NewsQueryFilters(keyword_query="nvidia"))

    assert exc_info.value.code == "apiKeyInvalid"
    assert exc_info.value.retryable is False
