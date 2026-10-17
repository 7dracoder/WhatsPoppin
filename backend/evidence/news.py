"""Fetch-through-cache for a ticker's news: used by GET /evidence/news and the prefetch command."""

import asyncio
import logging
from datetime import datetime

from backend.entities import EntityAlias, load_entities
from backend.evidence.news_cache import NewsStore, bucket, is_fresh
from backend.ingestion.news_api import ContentItem, NewsApiError, NewsApiGateway, NewsQueryFilters, poll_news

logger = logging.getLogger(__name__)


def aliases_for(ticker: str) -> EntityAlias:
    """Seed-list entry for the ticker, or a bare entry so unknown tickers still search."""
    for entity in load_entities():
        if entity.symbol.upper() == ticker:
            return entity
    return EntityAlias(symbol=ticker)


def fetch_live(ticker: str, start: datetime, end: datetime, gateway: NewsApiGateway) -> list[ContentItem]:
    """One metered NewsAPI call: the ticker plus its aliases, quoted so multi-word names stay phrases."""
    entity = aliases_for(ticker)
    terms = [ticker, *entity.aliases]
    filters = NewsQueryFilters(boolean_terms="(" + " OR ".join(f'"{t}"' for t in terms) + ")",
                               from_time=start, to_time=end)
    return poll_news(filters, gateway, [entity])


async def news_for(ticker: str, start: datetime, end: datetime, gateway: NewsApiGateway,
                   store: NewsStore | None) -> tuple[list[ContentItem], str]:
    """Items for the window and where they came from: "cache", "live", or "stale" (NewsAPI failed,
    the ticker's most recent cached window is served). Raises NewsApiError only when there is
    nothing cached to fall back on. A failing cache is treated as absent, never as an error.
    """
    start, end = bucket(start, end)
    if store is not None:
        try:
            cached = await store.get(ticker, start, end)
            if cached and is_fresh(cached, end):
                return cached.items, "cache"
        except Exception:  # noqa: BLE001 - the cache must never break the route
            logger.warning("News cache read failed for %s; fetching live", ticker, exc_info=True)
    try:
        items = await asyncio.to_thread(fetch_live, ticker, start, end, gateway)
    except NewsApiError as exc:
        if store is not None:
            try:
                stale = await store.latest(ticker)
            except Exception:  # noqa: BLE001
                stale = None
            if stale:
                logger.warning("NewsAPI failed (%s); serving %s's last cached window", exc, ticker)
                return stale.items, "stale"
        raise
    if store is not None:
        try:
            await store.put(ticker, start, end, items)
        except Exception:  # noqa: BLE001
            logger.warning("News cache write failed for %s", ticker, exc_info=True)
    return items, "live"
