"""Data models for the news_api ingestion layer.

Pydantic, not plain dataclasses: this module sits at an external boundary
(caller-supplied filters, NewsAPI-supplied JSON), so validation happens once
at construction time instead of being hand-checked deeper in the call stack.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class SortBy(str, Enum):
    RELEVANCY = "relevancy"
    POPULARITY = "popularity"
    PUBLISHED_AT = "publishedAt"


class SearchField(str, Enum):
    TITLE = "title"
    DESCRIPTION = "description"
    CONTENT = "content"


class NewsQueryFilters(BaseModel):
    source_ids: list[str] | None = None
    keyword_query: str | None = None
    exact_phrases: list[str] | None = None
    boolean_terms: str | None = None
    search_in: list[SearchField] | None = None
    domains: list[str] | None = None
    exclude_domains: list[str] | None = None
    language: str = "en"
    from_time: datetime | None = None
    to_time: datetime | None = None
    sort_by: SortBy = SortBy.PUBLISHED_AT
    page_size: int = Field(default=100, ge=1, le=100)
    page: int = Field(default=1, ge=1)

    @field_validator("source_ids")
    @classmethod
    def _max_20_sources(cls, v: list[str] | None) -> list[str] | None:
        if v and len(v) > 20:
            raise ValueError("NewsAPI allows at most 20 source IDs per call")
        return v

    # "At least one of keyword_query / exact_phrases / boolean_terms must be
    # set" is a cross-field rule and is enforced in
    # query_builder.build_query_string instead, where the error message can
    # name exactly which fields were empty.


class ContentItem(BaseModel):
    source: str = "news"
    id: str  # the article's url — NewsAPI gives articles no native id
    author: str | None = None  # NewsAPI source.name (outlet), not a byline
    title: str
    text: str | None = None  # NewsAPI "content"; truncated ~200 chars on free tier
    entities: list[str] = []
    url: str
    published_at: datetime


class NewsApiError(Exception):
    """Raised for any non-ok NewsAPI response.

    `retryable` is a signal for whatever scheduler calls this layer later —
    this module never retries on its own.
    """

    def __init__(self, code: str, message: str, retryable: bool):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"[{code}] {message}")
