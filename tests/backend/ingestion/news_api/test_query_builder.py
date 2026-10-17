import pytest

from backend.ingestion.news_api.models import NewsQueryFilters, SearchField, SortBy
from backend.ingestion.news_api.query_builder import build_query_string, build_request_kwargs


def test_keyword_only():
    filters = NewsQueryFilters(keyword_query="tesla")
    assert build_query_string(filters) == "tesla"


def test_exact_phrase_only():
    filters = NewsQueryFilters(exact_phrases=["electric vehicle recall"])
    assert build_query_string(filters) == '"electric vehicle recall"'


def test_multiple_exact_phrases():
    filters = NewsQueryFilters(exact_phrases=["chip shortage", "supply chain"])
    assert build_query_string(filters) == '"chip shortage" AND "supply chain"'


def test_keyword_and_phrase_combined():
    filters = NewsQueryFilters(keyword_query="tesla", exact_phrases=["gigafactory"])
    assert build_query_string(filters) == 'tesla AND "gigafactory"'


def test_boolean_terms_only():
    filters = NewsQueryFilters(boolean_terms="(tesla OR rivian) AND NOT recall")
    assert build_query_string(filters) == "(tesla OR rivian) AND NOT recall"


def test_all_empty_raises():
    filters = NewsQueryFilters()
    with pytest.raises(ValueError, match="At least one of"):
        build_query_string(filters)


def test_build_request_kwargs_minimal():
    filters = NewsQueryFilters(keyword_query="nvidia")
    kwargs = build_request_kwargs(filters)
    assert kwargs["q"] == "nvidia"
    assert kwargs["language"] == "en"
    assert kwargs["sort_by"] == SortBy.PUBLISHED_AT.value
    assert kwargs["page_size"] == 100
    assert kwargs["page"] == 1
    assert "sources" not in kwargs
    assert "domains" not in kwargs


def test_build_request_kwargs_full():
    filters = NewsQueryFilters(
        keyword_query="nvidia",
        source_ids=["the-verge", "techcrunch"],
        domains=["theverge.com"],
        exclude_domains=["spam.com"],
        search_in=[SearchField.TITLE],
    )
    kwargs = build_request_kwargs(filters)
    assert kwargs["sources"] == "the-verge,techcrunch"
    assert kwargs["domains"] == "theverge.com"
    assert kwargs["exclude_domains"] == "spam.com"
    assert kwargs["search_in"] == "title"


def test_source_ids_over_limit_rejected_by_model():
    with pytest.raises(ValueError, match="at most 20"):
        NewsQueryFilters(source_ids=[f"source-{i}" for i in range(21)])
