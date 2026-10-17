from datetime import date

from backend.ingestion.news_api import query_translator
from backend.ingestion.news_api.models import NewsQueryFilters, SortBy


def test_build_filters_from_query_delegates_to_llm_client(monkeypatch):
    captured = {}

    def fake_complete_structured(system_prompt, user_query, response_model):
        captured["system_prompt"] = system_prompt
        captured["user_query"] = user_query
        captured["response_model"] = response_model
        return NewsQueryFilters(keyword_query="nvidia", sort_by=SortBy.PUBLISHED_AT)

    monkeypatch.setattr(query_translator, "complete_structured", fake_complete_structured)

    result = query_translator.build_filters_from_query("latest news about nvidia")

    assert captured["user_query"] == "latest news about nvidia"
    assert captured["response_model"] is NewsQueryFilters
    assert date.today().isoformat() in captured["system_prompt"]
    assert result.keyword_query == "nvidia"
    assert result.sort_by == SortBy.PUBLISHED_AT
