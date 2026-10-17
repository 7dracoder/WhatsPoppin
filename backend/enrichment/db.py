"""Postgres for market enrichment: the entity map in `entities`, which events need enriching,
saving results to `market_entities` and `market_enrichment`, and the search API's reads.

Importing the model modules below registers their tables on the shared Base.metadata, so the
shared connect() (which runs create_all) builds them too, as company_graph/db.py does.
"""

from datetime import datetime, timezone

from sqlalchemy import case, delete, func, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateIndex

from backend.entities import EntityMap
from backend.ingestion.common.db import DEMO_SERIES, connect
from backend.models import Market
from backend.models.entity import Entity
from backend.models.entity_relationship import EntityRelationship
from backend.models.market_enrichment import MarketEnrichment
from backend.models.market_entity import MarketEntity

__all__ = ["connect", "sync_entity_map", "events_to_enrich", "sibling_tags", "mark_pending", "save_result",
           "save_failure", "autocomplete", "get_entity", "markets_for_entity", "graph_links", "top_markets"]

MAX_ATTEMPTS = 3  # failures in a row under one map version before a market is left alone
ERROR_MAX_CHARS = 500
MARKET_COLUMNS = (Market.source, Market.market_id, Market.title, Market.outcome_label, Market.rules_primary,
                  Market.event_id, Market.event_title, Market.series_title, Market.category, Market.tags)
# Markets are enriched per event; a market without an event is its own.
EVENT_KEY = func.coalesce(Market.event_id, Market.market_id)


async def sync_entity_map(engine: AsyncEngine, entity_map: EntityMap) -> None:
    """Insert map entities missing from `entities`. Existing rows are left alone: the company
    graph keeps company names up to date from SEC, and a renamed map entry is a new symbol.

    Also adds the market_entities indexes, which create_all only builds with a new table.
    """
    stmt = pg_insert(Entity).values([{"symbol": e.symbol, "name": e.name, "type": e.category}
                                     for e in entity_map.entities])
    async with engine.begin() as conn:
        await conn.execute(stmt.on_conflict_do_nothing(index_elements=["symbol"]))
        for index in MarketEntity.__table__.indexes:
            await conn.execute(CreateIndex(index, if_not_exists=True))


def _needs_enrichment(map_version: int):
    """Never enriched, left pending, enriched under an older map version, or failed fewer
    than MAX_ATTEMPTS times under this one. Use with an outer join to market_enrichment.
    """
    e = MarketEnrichment
    return or_(e.status.is_(None), e.status == "pending", e.map_version < map_version,
               (e.status == "failed") & (e.attempts < MAX_ATTEMPTS))


async def events_to_enrich(engine: AsyncEngine, map_version: int, limit: int) -> list[list[dict]]:
    """Up to `limit` events with active markets that need enriching, each as the list of those
    markets. Events with never-seen markets come first. An event is never split across batches.
    """
    e = MarketEnrichment
    joined = (e.source == Market.source) & (e.market_id == Market.market_id)
    wanted = (Market.status == "active", Market.series_id.is_distinct_from(DEMO_SERIES), _needs_enrichment(map_version))
    events = (select(Market.source, EVENT_KEY.label("event_key"))
              .outerjoin(e, joined).where(*wanted)
              .group_by(Market.source, EVENT_KEY)
              .order_by(func.bool_or(e.status.is_(None)).desc(), func.max(Market.updated_at).desc())
              .limit(limit)
              .subquery())
    query = (select(*MARKET_COLUMNS, events.c.event_key)
             .outerjoin(e, joined)
             .join(events, (events.c.source == Market.source) & (events.c.event_key == EVENT_KEY))
             .where(*wanted)
             .order_by(Market.source, events.c.event_key, Market.market_id))
    async with engine.connect() as conn:
        rows = [dict(r._mapping) for r in await conn.execute(query)]
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        grouped.setdefault((row["source"], row.pop("event_key")), []).append(row)
    return list(grouped.values())


async def sibling_tags(engine: AsyncEngine, source: str, event_id: str, entity_map: EntityMap) -> list[str] | None:
    """The map tags of a market in this event already enriched under this map version, or None
    if there is none. A market added to a tagged event (a new strike) copies these, with no Jev call.
    """
    e = MarketEnrichment
    done = (select(e.market_id)
            .join(Market, (Market.source == e.source) & (Market.market_id == e.market_id))
            .where(Market.source == source, Market.event_id == event_id, e.status == "done",
                   e.map_version == entity_map.version)
            .limit(1))
    async with engine.connect() as conn:
        market_id = (await conn.execute(done)).scalar()
        if market_id is None:
            return None
        tags = await conn.execute(select(MarketEntity.entity_symbol).where(
            MarketEntity.source == source, MarketEntity.market_id == market_id,
            MarketEntity.entity_symbol.in_(entity_map.symbols)))
        return list(tags.scalars())


async def mark_pending(engine: AsyncEngine, markets: list[dict]) -> None:
    """Mark a claimed batch 'pending'. A crash leaves them pending, so the next sweep retries them."""
    if not markets:
        return
    stmt = pg_insert(MarketEnrichment).values([{"source": m["source"], "market_id": m["market_id"],
                                                "status": "pending"} for m in markets])
    async with engine.begin() as conn:
        await conn.execute(stmt.on_conflict_do_update(index_elements=["source", "market_id"],
                                                      set_={"status": "pending"}))


async def save_result(engine: AsyncEngine, source: str, market_ids: list[str], symbols: list[str],
                      entity_map: EntityMap) -> None:
    """Give these markets (one event's) exactly `symbols` as map tags and mark them done, in one transaction.

    Only links to map entities are replaced: company_graph's F7 also writes `market_entities`,
    for companies that may not be in the map, and those rows are not this worker's to delete.
    """
    now = datetime.now(timezone.utc)
    async with engine.begin() as conn:
        await conn.execute(delete(MarketEntity).where(
            MarketEntity.source == source, MarketEntity.market_id.in_(market_ids),
            MarketEntity.entity_symbol.in_(entity_map.symbols)))
        if symbols:
            await conn.execute(pg_insert(MarketEntity).values(
                [{"source": source, "market_id": m, "entity_symbol": s}
                 for m in market_ids for s in dict.fromkeys(symbols)]
            ).on_conflict_do_nothing())
        values = {"status": "done", "map_version": entity_map.version, "attempts": 0, "enriched_at": now,
                  "error": None}
        stmt = pg_insert(MarketEnrichment).values([{"source": source, "market_id": m, **values} for m in market_ids])
        await conn.execute(stmt.on_conflict_do_update(index_elements=["source", "market_id"], set_=values))


async def save_failure(engine: AsyncEngine, source: str, market_ids: list[str], error: str,
                       map_version: int) -> None:
    """Mark these markets failed. Earlier links stay; `attempts` counts failures under this map version."""
    e = MarketEnrichment
    stmt = pg_insert(e).values([{"source": source, "market_id": m, "status": "failed", "map_version": map_version,
                                 "attempts": 1, "error": error[:ERROR_MAX_CHARS]} for m in market_ids])
    stmt = stmt.on_conflict_do_update(index_elements=["source", "market_id"], set_={
        "status": "failed",
        "attempts": case((e.map_version == map_version, e.attempts + 1), else_=1),
        "map_version": map_version,
        "error": stmt.excluded.error,
    })
    async with engine.begin() as conn:
        await conn.execute(stmt)


# --- reads for the search API ------------------------------------------------------

async def autocomplete(engine: AsyncEngine, q: str, symbols: list[str], category: str | None,
                       limit: int) -> list[dict]:
    """Map entities whose name (or symbol) starts with `q`, case-insensitive, shortest name first."""
    prefix = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    query = (select(Entity.symbol, Entity.name, Entity.type)
             .where(Entity.symbol.in_(symbols),
                    or_(Entity.name.ilike(prefix, escape="\\"), Entity.symbol.ilike(prefix, escape="\\")))
             .order_by(func.length(Entity.name), Entity.name)
             .limit(limit))
    if category is not None:
        query = query.where(Entity.type == category)
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(query)]


async def get_entity(engine: AsyncEngine, symbol: str) -> dict | None:
    async with engine.connect() as conn:
        row = (await conn.execute(select(Entity.symbol, Entity.name, Entity.type)
                                  .where(Entity.symbol == symbol))).first()
    return dict(row._mapping) if row else None


async def markets_for_entity(engine: AsyncEngine, symbol: str, limit: int) -> list[dict]:
    """Every market linked to this entity, across all sources. Open markets first, soonest close first."""
    query = (select(Market.source, Market.market_id, Market.title, Market.outcome_label, Market.event_title,
                    Market.status, Market.close_time, Market.url)
             .join(MarketEntity, (MarketEntity.source == Market.source)
                   & (MarketEntity.market_id == Market.market_id))
             .where(MarketEntity.entity_symbol == symbol)
             .order_by((Market.status == "active").desc(), Market.close_time.asc().nulls_last(), Market.market_id)
             .limit(limit))
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(query)]


async def graph_links(engine: AsyncEngine, symbol: str) -> list[str]:
    """Companies the company graph links to `symbol` from filings, in either direction.
    Sector-peer links are left out: they are a weak SIC-code fallback.
    """
    r = EntityRelationship
    other = case((r.entity_symbol == symbol, r.related_entity_symbol), else_=r.entity_symbol)
    query = (select(other).distinct()
             .where(or_(r.entity_symbol == symbol, r.related_entity_symbol == symbol), r.source == "filing"))
    async with engine.connect() as conn:
        return sorted((await conn.execute(query)).scalars())


async def top_markets(engine: AsyncEngine, tiers: dict[str, int], limit: int) -> list[dict]:
    """The best active market per series among markets tagged with any of `tiers`' entities
    (entity symbol -> tier, 1 = best), ranked by best tier, then most tags matched, then soonest
    to close. One query.

    One market per series (a market without one: per event): a series is one recurring question
    ("NVIDIA A100 average hourly price", one event per month), so without this the top results
    are the same question for different months, or the strikes of one event.
    """
    if not tiers:
        return []
    tags = union_all(*(select(literal(s).label("symbol"), literal(t).label("tier")) for s, t in tiers.items())).cte("tags")
    group_key = func.coalesce(Market.series_id, Market.event_id, Market.market_id).label("group_key")
    hits = (select(Market.source, Market.market_id, Market.event_id, Market.series_id, group_key, Market.title,
                   Market.outcome_label, Market.event_title, Market.close_time, Market.url, Market.rules_primary,
                   func.min(tags.c.tier).label("tier"),
                   func.array_agg(MarketEntity.entity_symbol.distinct()).label("matched"))
            .select_from(tags)
            .join(MarketEntity, MarketEntity.entity_symbol == tags.c.symbol)
            .join(Market, (Market.source == MarketEntity.source) & (Market.market_id == MarketEntity.market_id))
            .where(Market.status == "active")
            .group_by(Market.source, Market.market_id)
            .cte("hits"))
    per_series = (select(hits).distinct(hits.c.source, hits.c.group_key)
                  .order_by(hits.c.source, hits.c.group_key, hits.c.tier, func.cardinality(hits.c.matched).desc(),
                            hits.c.close_time.asc().nulls_last(), hits.c.market_id)
                  .subquery())
    query = (select(per_series)
             .order_by(per_series.c.tier, func.cardinality(per_series.c.matched).desc(),
                       per_series.c.close_time.asc().nulls_last())
             .limit(limit))
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(query)]
