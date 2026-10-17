"""Maps raw X API v2 tweet dicts to ContentItem.

Stays a pure 1:1 field mapping — entity tagging happens in service.py, not
here, same convention as ingestion/news_api/normalize.py.
"""

import logging

from .models import ContentItem, Engagement

logger = logging.getLogger(__name__)


def normalize_tweet(raw: dict) -> ContentItem:
    """Map one raw X API v2 tweet dict (with public_metrics, created_at, and
    _author_username attached by TwitterApiGateway) to a ContentItem.

    id          <- raw["id"]
    author      <- raw["_author_username"]      (resolved by the gateway,
                                                  via expansions=author_id or
                                                  the looked-up account itself)
    text        <- raw["text"]
    published_at <- raw["created_at"]
    engagement  <- raw["public_metrics"]

    Raises ValueError if id or text is missing — both required.
    """
    tweet_id = raw.get("id")
    text = raw.get("text")
    if not tweet_id:
        raise ValueError("tweet missing required field: id")
    if not text:
        raise ValueError("tweet missing required field: text")

    metrics = raw.get("public_metrics") or {}
    author = raw.get("_author_username")

    return ContentItem(
        id=str(tweet_id),
        author=author,
        text=text,
        url=f"https://x.com/{author or 'i'}/status/{tweet_id}",
        published_at=raw["created_at"],
        engagement=Engagement(
            likes=metrics.get("like_count", 0),
            reposts=metrics.get("retweet_count", 0),
            replies=metrics.get("reply_count", 0),
            quotes=metrics.get("quote_count", 0),
        ),
    )


def normalize_batch(raw_tweets: list[dict]) -> list[ContentItem]:
    """normalize_tweet over a list; skips (logs, doesn't raise on) any tweet
    that fails normalize_tweet's validation, so one malformed tweet doesn't
    fail the whole lookup.
    """
    items: list[ContentItem] = []
    for raw in raw_tweets:
        try:
            items.append(normalize_tweet(raw))
        except (ValueError, KeyError) as exc:
            logger.warning("skipping malformed tweet: %s", exc)
    return items
