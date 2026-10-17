"""Cache for per-item sentiment, so a paid Jev call is made once per distinct text.

The store is a small Protocol rather than a concrete Postgres class so the API can be tested
with an in-memory fake (there is no async SQLite driver in this project), and so the API still
works, just uncached, when no database is configured.
"""

import hashlib
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.models.sentiment_cache import SentimentCache

# Bump when the sentiment question or model changes: every old key then misses, which
# invalidates the whole cache without a migration or a DELETE.
CACHE_VERSION = "sentiment-v1"


@dataclass(frozen=True)
class CachedSentiment:
    label: str
    probability: float | None


def content_key(title: str, text: str | None) -> str:
    """Stable key for what Jev actually sees: the title and body, plus the cache version."""
    digest = hashlib.sha256()
    for part in (CACHE_VERSION, title, text or ""):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")  # separator, so ("ab", "c") and ("a", "bc") differ
    return digest.hexdigest()


class SentimentStore(Protocol):
    async def get_many(self, keys: list[str]) -> dict[str, CachedSentiment]: ...

    async def put_many(self, entries: dict[str, CachedSentiment]) -> None: ...


class PostgresSentimentStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]):
        self._sessionmaker = sessionmaker

    async def get_many(self, keys: list[str]) -> dict[str, CachedSentiment]:
        if not keys:
            return {}
        async with self._sessionmaker() as session:
            rows = await session.execute(select(SentimentCache).where(SentimentCache.content_hash.in_(keys)))
            return {r.content_hash: CachedSentiment(r.label, r.probability) for r in rows.scalars()}

    async def put_many(self, entries: dict[str, CachedSentiment]) -> None:
        if not entries:
            return
        async with self._sessionmaker() as session:
            stmt = pg_insert(SentimentCache).values(
                [{"content_hash": k, "label": v.label, "probability": v.probability} for k, v in entries.items()]
            )
            # Two requests can classify the same new text at once; the first write wins.
            await session.execute(stmt.on_conflict_do_nothing(index_elements=["content_hash"]))
            await session.commit()
