"""Entry point for the twitter_lookup ingestion module. The rest of the
codebase (a future FastAPI route for the chart-triggered account lookup,
or alert_detector's enricher for the keyword-search trigger) should only
import lookup_account / lookup_keyword — everything else in this package
is an implementation detail reachable through them.
"""

from datetime import datetime

from .client import TwitterApiGateway
from .entity_match import EntityAlias, EntityMatcher
from .models import ContentItem
from .normalize import normalize_batch

__all__ = ["lookup_account", "lookup_keyword"]


def lookup_account(
    username: str,
    start: datetime,
    end: datetime,
    gateway: TwitterApiGateway,
    entities: list[EntityAlias],
) -> list[ContentItem]:
    """On-demand backfill of one account's tweets in [start, end].

    Does not: promote the account to twitter_watchlist, retry on
    TwitterApiError, classify the tweets, decide what's "hot" from
    engagement counts — all out of scope for this layer (see
    new_specs/ingestion/twitter-lookup.md Non-Goals).
    """
    raw_tweets = gateway.fetch_user_timeline(username, start, end)
    return _tag_entities(normalize_batch(raw_tweets), entities)


def lookup_keyword(
    query: str,
    start: datetime,
    end: datetime,
    gateway: TwitterApiGateway,
    entities: list[EntityAlias],
) -> list[ContentItem]:
    """On-demand keyword/topic search in [start, end] — e.g. triggered by
    alert_detector's enricher around a price-move alert. `query` is
    typically built with query_builder.build_keyword_query() from a
    market's title/event_title/tags.

    Does not: build the query itself, retry on TwitterApiError, classify
    the tweets — all out of scope for this layer.
    """
    raw_tweets = gateway.fetch_recent_search(query, start, end)
    return _tag_entities(normalize_batch(raw_tweets), entities)


def _tag_entities(items: list[ContentItem], entities: list[EntityAlias]) -> list[ContentItem]:
    matcher = EntityMatcher(entities)
    for item in items:
        item.entities = matcher.match(item.text)
    return items
