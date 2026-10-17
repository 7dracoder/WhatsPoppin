"""Maps raw NewsAPI /v2/everything article dicts to ContentItem.

Stays a pure 1:1 field mapping — entity tagging happens in service.py, not
here, so this module's only job is "does this raw dict become a valid
ContentItem."
"""

import logging

from .models import ContentItem

logger = logging.getLogger(__name__)


def normalize_article(raw: dict) -> ContentItem:
    """Map one raw NewsAPI article dict to a ContentItem.

    id/url    <- raw["url"]            (NewsAPI gives articles no native id)
    author    <- raw["source"]["name"]  (outlet, not a byline — raw["author"]
                                         is often null or an unreliable
                                         byline string)
    title     <- raw["title"]
    text      <- raw["content"]         (may be None; truncated on free tier)
    published_at <- raw["publishedAt"]

    Raises ValueError if url or title is missing/empty — both are required
    (id and display), but content/author are allowed to be None since
    NewsAPI returns null-content stubs for removed/paywalled articles that
    still carry a url and title.
    """
    url = raw.get("url")
    title = raw.get("title")
    if not url:
        raise ValueError("article missing required field: url")
    if not title:
        raise ValueError("article missing required field: title")

    source = raw.get("source") or {}

    return ContentItem(
        id=url,
        author=source.get("name"),
        title=title,
        text=raw.get("content"),
        url=url,
        published_at=raw["publishedAt"],
    )


def normalize_batch(raw_articles: list[dict]) -> list[ContentItem]:
    """normalize_article over a list; skips (logs, doesn't raise on) any
    article that fails normalize_article's validation, so one malformed
    article doesn't fail the whole poll.
    """
    items: list[ContentItem] = []
    for raw in raw_articles:
        try:
            items.append(normalize_article(raw))
        except (ValueError, KeyError) as exc:
            logger.warning("skipping malformed article: %s", exc)
    return items
