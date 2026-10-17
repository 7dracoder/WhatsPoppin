"""Entry point for the news_api ingestion layer. The rest of the codebase
should only import poll_news — everything else in this package is an
implementation detail reachable through it.
"""

from backend.entities import EntityAlias, EntityMatcher

from .client import NewsApiGateway
from .models import ContentItem, NewsQueryFilters
from .normalize import normalize_batch


def poll_news(
    filters: NewsQueryFilters,
    gateway: NewsApiGateway,
    entities: list[EntityAlias],
) -> list[ContentItem]:
    """Run one poll: fetch, normalize, tag with entities.

    Does not: schedule itself, retry on NewsApiError, de-dup against a
    previous poll, or filter on content quality — all out of scope for
    this layer (see new_specs/ingestion/news-aggregator.md Open Questions).
    """
    raw_articles = gateway.fetch_raw(filters)
    items = normalize_batch(raw_articles)

    matcher = EntityMatcher(entities)
    for item in items:
        item.entities = matcher.match(item.title, item.text)

    return items
