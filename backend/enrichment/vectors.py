"""Market and entity embeddings: the retrieval step in front of Jev.

- `ensure_event_embeddings`: embed every given event whose text has no vector yet (or changed),
  in batches, and store them in `market_embeddings`.
- `search_events`: a free-text query against every active event's vector, best first.
- `EntityIndex`: the entity map embedded once per process; `shortlist(event_vector, k)` gives the
  k map entities closest to an event, which enrichment then asks Jev about in one call.

All vectors are normalized, so similarity is a dot product (backend/llm/embeddings.py).
"""

import asyncio
import hashlib
import time

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.enrichment.db import EVENT_KEY
from backend.enrichment.enrich import event_text
from backend.entities import CATEGORIES, EntityMap, MapEntity
from backend.llm.embeddings import embed, embed_model, top_k
from backend.models import Market
from backend.models.market_embedding import MarketEmbedding

EVENT_COLUMNS = (Market.source, Market.market_id, Market.event_id, Market.series_id, Market.title,
                 Market.outcome_label, Market.rules_primary, Market.event_title, Market.series_title,
                 Market.category, Market.tags, Market.close_time, Market.url)
INDEX_TTL_S = 60.0  # how long search reuses the in-memory matrix before checking for new vectors


def embedding_text(markets: list[dict]) -> str:
    """The text one event is embedded as: the same question + outcomes + context Jev sees."""
    title, text = event_text(markets)
    return f"{title}\n{text}" if text else title


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


async def active_events(engine: AsyncEngine) -> dict[tuple[str, str], list[dict]]:
    """Every active market grouped by (source, event key), markets soonest-closing first."""
    query = (select(*EVENT_COLUMNS, EVENT_KEY.label("event_key"))
             .where(Market.status == "active")
             .order_by(Market.source, EVENT_KEY, Market.close_time.asc().nulls_last(), Market.market_id))
    async with engine.connect() as conn:
        rows = [dict(r._mapping) for r in await conn.execute(query)]
    events: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        events.setdefault((row["source"], row.pop("event_key")), []).append(row)
    return events


async def ensure_event_embeddings(engine: AsyncEngine, events: dict[tuple[str, str], list[dict]],
                                  model: str | None = None, embed_fn=embed) -> dict[tuple[str, str], np.ndarray]:
    """A vector for every event in `events`, embedding (and storing) only the new or changed ones."""
    model = model or embed_model()
    if not events:
        return {}
    texts = {key: embedding_text(markets) for key, markets in events.items()}
    hashes = {key: text_hash(t) for key, t in texts.items()}
    e = MarketEmbedding
    stored: dict[tuple[str, str], np.ndarray] = {}
    async with engine.connect() as conn:
        for source in {s for s, _ in events}:
            keys = [k for s, k in events if s == source]
            rows = await conn.execute(select(e.event_key, e.text_hash, e.vector)
                                      .where(e.source == source, e.model == model, e.event_key.in_(keys)))
            for event_key, h, vector in rows:
                if hashes.get((source, event_key)) == h:
                    stored[(source, event_key)] = np.asarray(vector, dtype=np.float32)
    missing = [key for key in events if key not in stored]
    if missing:
        vectors = await asyncio.to_thread(embed_fn, [texts[k] for k in missing], "document", model)
        values = [{"source": s, "event_key": k, "model": model, "text_hash": hashes[(s, k)],
                   "vector": [float(x) for x in v]} for (s, k), v in zip(missing, vectors)]
        async with engine.begin() as conn:
            for i in range(0, len(values), 200):
                stmt = pg_insert(e).values(values[i:i + 200])
                await conn.execute(stmt.on_conflict_do_update(
                    index_elements=["source", "event_key", "model"],
                    set_={"text_hash": stmt.excluded.text_hash, "vector": stmt.excluded.vector,
                          "created_at": func.now()}))
        stored.update(dict(zip(missing, vectors)))
    return stored


class _SearchIndex:
    """Active events and their vectors, rebuilt at most every INDEX_TTL_S."""

    def __init__(self) -> None:
        self.built_at = 0.0
        self.model = ""
        self.keys: list[tuple[str, str]] = []
        self.events: dict[tuple[str, str], list[dict]] = {}
        self.matrix = np.zeros((0, 0), dtype=np.float32)
        self.lock = asyncio.Lock()

    async def refresh(self, engine: AsyncEngine, model: str, embed_fn) -> None:
        async with self.lock:
            if self.model == model and time.monotonic() - self.built_at < INDEX_TTL_S:
                return
            events = await active_events(engine)
            vectors = await ensure_event_embeddings(engine, events, model, embed_fn)
            self.keys = [k for k in events if k in vectors]
            self.events = events
            self.matrix = np.stack([vectors[k] for k in self.keys]) if self.keys else np.zeros((0, 0), np.float32)
            self.model, self.built_at = model, time.monotonic()


_INDEX = _SearchIndex()


async def search_events(engine: AsyncEngine, query: str, limit: int, min_score: float,
                        embed_fn=embed) -> list[tuple[list[dict], float]]:
    """(the event's markets, similarity) for the events closest to `query`, best first.
    The first search after start embeds every active event not embedded yet."""
    model = embed_model()
    await _INDEX.refresh(engine, model, embed_fn)
    if not _INDEX.keys:
        return []
    q = (await asyncio.to_thread(embed_fn, [query], "query", model))[0]
    hits = top_k(q, _INDEX.matrix, limit)
    return [(_INDEX.events[_INDEX.keys[i]], score) for i, score in hits if score >= min_score]


def entity_text(entity: MapEntity) -> str:
    """What an entity is embedded as: its name, ticker, aliases and what its category means."""
    parts = [entity.name]
    if entity.category == "company":
        parts.append(f"({entity.symbol})")
    text = " ".join(parts) + f": {CATEGORIES[entity.category]}"
    if entity.aliases:
        text += f". Also known as {', '.join(entity.aliases)}"
    return text


class EntityIndex:
    """The entity map's vectors, embedded once (one call per 128 entities) and kept in memory."""

    def __init__(self, entity_map: EntityMap, embed_fn=embed, model: str | None = None) -> None:
        self.entities = entity_map.entities
        self.matrix = embed_fn([entity_text(e) for e in self.entities], "document", model or embed_model())

    def shortlist(self, event_vector: np.ndarray, k: int, categories: list[str] | None = None) -> list[MapEntity]:
        """The k entities closest to the event, best first; with `categories`, the k closest within
        EACH of those categories. Per category matters: across the whole map, broad entities
        ("United States", a head of state) rank far below the specific themes an event names."""
        if categories is None:
            return [self.entities[i] for i, _ in top_k(event_vector, self.matrix, k)]
        out: list[MapEntity] = []
        for category in categories:
            rows = [i for i, e in enumerate(self.entities) if e.category == category]
            if rows:
                out += [self.entities[rows[j]] for j, _ in top_k(event_vector, self.matrix[rows], k)]
        return out
