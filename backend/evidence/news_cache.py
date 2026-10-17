"""Cache for /evidence/news answers (one row per ticker and day-bucketed window).

Same shape as classification/sentiment_cache: a Protocol so the route can be tested with an
in-memory fake and still works, uncached, with no database. Keyed by whole UTC days so two
requests made minutes apart (the frontend builds its window from Date.now()) share one row.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.ingestion.news_api import ContentItem
from backend.models.news_cache import NewsCache

NEWS_REFRESH_S = 6 * 3600  # a window that includes today is re-fetched this often
DAY = timedelta(days=1)


@dataclass(frozen=True)
class CachedNews:
    items: list[ContentItem]
    fetched_at: datetime


def bucket(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Round the window out to whole UTC days: start down, end up."""
    start = start.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if end_day < end.astimezone(timezone.utc):
        end_day += DAY
    return start, max(end_day, start + DAY)


def is_fresh(cached: CachedNews, window_end: datetime, now: datetime | None = None) -> bool:
    """A window that has fully passed never changes; a live one goes stale after NEWS_REFRESH_S."""
    now = now or datetime.now(timezone.utc)
    if window_end <= now - timedelta(hours=1):  # NewsAPI's own indexing lag
        return True
    return (now - cached.fetched_at).total_seconds() < NEWS_REFRESH_S


class NewsStore(Protocol):
    async def get(self, ticker: str, start: datetime, end: datetime) -> CachedNews | None: ...

    async def put(self, ticker: str, start: datetime, end: datetime, items: list[ContentItem]) -> None: ...

    async def latest(self, ticker: str) -> CachedNews | None:
        """The most recently fetched window for the ticker, whatever its dates: served when NewsAPI is down."""
        ...


class PostgresNewsStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]):
        self._sessionmaker = sessionmaker

    async def get(self, ticker: str, start: datetime, end: datetime) -> CachedNews | None:
        async with self._sessionmaker() as session:
            row = (await session.execute(select(NewsCache).where(
                NewsCache.ticker == ticker, NewsCache.window_start == start, NewsCache.window_end == end))).scalar()
        return _to_cached(row) if row else None

    async def put(self, ticker: str, start: datetime, end: datetime, items: list[ContentItem]) -> None:
        values = {"ticker": ticker, "window_start": start, "window_end": end,
                  "items": [i.model_dump(mode="json") for i in items], "fetched_at": datetime.now(timezone.utc)}
        async with self._sessionmaker() as session:
            stmt = pg_insert(NewsCache).values(**values)
            # excluded["items"], not excluded.items: .items is the column collection's dict method.
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "window_start", "window_end"],
                set_={"items": stmt.excluded["items"], "fetched_at": stmt.excluded.fetched_at},
            )
            await session.execute(stmt)
            await session.commit()

    async def latest(self, ticker: str) -> CachedNews | None:
        async with self._sessionmaker() as session:
            row = (await session.execute(select(NewsCache).where(NewsCache.ticker == ticker)
                                         .order_by(NewsCache.fetched_at.desc()).limit(1))).scalar()
        return _to_cached(row) if row else None


def _to_cached(row: NewsCache) -> CachedNews:
    return CachedNews([ContentItem.model_validate(i) for i in row.items], row.fetched_at)


__all__ = ["CachedNews", "NewsStore", "PostgresNewsStore", "bucket", "is_fresh", "NEWS_REFRESH_S", "insert"]
