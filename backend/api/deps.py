"""Shared FastAPI dependencies: one gateway instance per ingestion module,
built once from .env rather than re-reading env vars on every request.
"""

import asyncio
import logging
import os
import time
from functools import lru_cache

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from backend.classification.sentiment_cache import PostgresSentimentStore, SentimentStore
from backend.evidence.news_cache import NewsStore, PostgresNewsStore
from backend.ingestion.common.db import make_engine
from backend.models.base import Base
from backend.models.news_cache import NewsCache
from backend.models.sentiment_cache import SentimentCache

from backend.ingestion.alpaca import AlpacaApiGateway
from backend.ingestion.kalshi.kalshi import REST_URL as KALSHI_REST_URL
from backend.ingestion.news_api import NewsApiGateway
from backend.ingestion.twitter_lookup import TwitterApiGateway

logger = logging.getLogger(__name__)


@lru_cache
def get_alpaca_gateway() -> AlpacaApiGateway:
    key_id = os.environ.get("ALPACA_API_KEY_ID")
    secret = os.environ.get("ALPACA_API_SECRET_KEY")
    if not key_id or not secret:
        # 503 like Twitter below: the service is up, Alpaca just isn't configured.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Alpaca is not configured: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in .env")
    return AlpacaApiGateway(key_id=key_id, secret_key=secret)


@lru_cache
def get_twitter_gateway() -> TwitterApiGateway:
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        # 503, not a bare RuntimeError (a 500 with a traceback): the service is up, X isn't configured.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Twitter is not configured: set X_BEARER_TOKEN in .env")
    return TwitterApiGateway(bearer_token=token)


@lru_cache
def get_kalshi_client() -> httpx.Client:
    # Kalshi's market-data GETs (candlesticks) are public, so no credentials
    # are needed; the signed headers in kalshi.py are only for the WebSocket.
    return httpx.Client(base_url=KALSHI_REST_URL, timeout=15)


@lru_cache
def get_news_gateway() -> NewsApiGateway | None:
    # Returns None (not raises) when unconfigured: /evidence/news degrades to
    # clearly-marked mock data so the UI still works without a NewsAPI key.
    key = os.environ.get("NEWSAPI_KEY")
    return NewsApiGateway(api_key=key) if key else None


_engine: AsyncEngine | None = None
_engine_lock = asyncio.Lock()


async def get_db_engine() -> AsyncEngine:
    """The shared DB engine, built on the first request (like projects.py), so the API still
    starts without DATABASE_URL. Read-only routers use it; the workers create the tables.
    """
    global _engine
    if _engine is None:
        async with _engine_lock:  # concurrent first requests must not each build an engine
            if _engine is None:
                url = os.environ.get("DATABASE_URL")
                if not url:
                    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Set DATABASE_URL in .env")
                _engine = make_engine(url)
    return _engine


_sentiment_store: SentimentStore | None = None
_sentiment_store_failed_at = 0.0
SENTIMENT_STORE_RETRY_S = 60.0


async def get_sentiment_store() -> SentimentStore | None:
    """The sentiment cache, or None when there's no usable database.

    None (never an error) keeps /classification/sentiment working, just uncached. After a
    failed connection it doesn't retry for a minute, so a down database costs one slow
    request, not one per call.
    """
    global _sentiment_store, _sentiment_store_failed_at
    if _sentiment_store is not None:
        return _sentiment_store
    if time.monotonic() - _sentiment_store_failed_at < SENTIMENT_STORE_RETRY_S:
        return None
    try:
        engine = await get_db_engine()
        async with engine.begin() as conn:
            # Only this table: the shared DB's other tables belong to their own packages.
            await conn.run_sync(Base.metadata.create_all, tables=[SentimentCache.__table__])
        _sentiment_store = PostgresSentimentStore(async_sessionmaker(engine, expire_on_commit=False))
    except Exception as exc:  # no DATABASE_URL (HTTPException) or an unreachable database
        logger.warning("Sentiment cache unavailable (%s); classifying without it", exc)
        _sentiment_store_failed_at = time.monotonic()
        return None
    return _sentiment_store


_news_store: NewsStore | None = None
_news_store_failed_at = 0.0


async def get_news_store() -> NewsStore | None:
    """The news cache, or None when there's no usable database (the route then fetches live every time)."""
    global _news_store, _news_store_failed_at
    if _news_store is not None:
        return _news_store
    if time.monotonic() - _news_store_failed_at < SENTIMENT_STORE_RETRY_S:
        return None
    try:
        engine = await get_db_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[NewsCache.__table__])
        _news_store = PostgresNewsStore(async_sessionmaker(engine, expire_on_commit=False))
    except Exception as exc:  # noqa: BLE001 - no DATABASE_URL or an unreachable database
        logger.warning("News cache unavailable (%s); fetching news uncached", exc)
        _news_store_failed_at = time.monotonic()
        return None
    return _news_store
