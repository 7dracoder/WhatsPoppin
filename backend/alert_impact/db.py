"""Postgres for alert impact: read alerts and their market's tags, store impacts.

Read-only on `alerts` and `markets`. Writes `alert_impacts`, `alert_impact_runs`, and
`graph_events` rows for tier-1 companies (exactly like company graph F7), so the highlight
builder (F8) spreads them to linked companies and the workspace graph shows them.
Importing the model modules registers their tables, so the shared connect() creates them.
"""

from datetime import datetime, timezone

from sqlalchemy import case, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.alert_impact.match import Impact
from backend.enrichment.db import EVENT_KEY, MARKET_COLUMNS, connect, sibling_tags
from backend.entities import EntityMap
from backend.models import Alert, Market
from backend.models.alert_impact import AlertImpact
from backend.models.alert_impact_run import AlertImpactRun
from backend.models.entity import Entity
from backend.models.entity_relationship import EntityRelationship
from backend.models.graph_event import GraphEvent
from backend.models.market_enrichment import MarketEnrichment
from backend.models.market_entity import MarketEntity

__all__ = ["connect", "add_columns", "all_companies", "load_alert", "alerts_to_process", "market_tags", "event_markets", "graph_links",
           "save_impacts", "save_run", "recent_alerts", "company_alerts"]

MAX_ATTEMPTS = 3
EVENT_SOURCE, EVENT_TYPE = "market", "odds_move"  # company graph F7's values for a market move


async def add_columns(engine: AsyncEngine) -> None:
    """Columns added after the table first shipped (create_all doesn't add columns)."""
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE alert_impacts ADD COLUMN IF NOT EXISTS relevance numeric"))


async def all_companies(engine: AsyncEngine) -> list[tuple[str, str]]:
    """Every company in `entities`: the map's 50 plus those the company graph added."""
    async with engine.connect() as conn:
        rows = await conn.execute(select(Entity.symbol, Entity.name).where(Entity.type == "company")
                                  .order_by(Entity.symbol))
        return [(s, n) for s, n in rows]


async def load_alert(engine: AsyncEngine, alert_id: int) -> dict | None:
    """The alert joined with its `markets` row (market fields are None when there is none)."""
    query = (select(Alert.id.label("alert_id"), Alert.created_at, Alert.direction, Alert.summary,
                    Alert.source, Alert.market_id, func.coalesce(Market.event_id, Alert.event_id).label("event_id"),
                    Market.title, Market.outcome_label, Market.rules_primary, Market.event_title,
                    Market.series_title, Market.category, Market.tags, Market.url)
             .outerjoin(Market, (Market.source == Alert.source) & (Market.market_id == Alert.market_id))
             .where(Alert.id == alert_id))
    async with engine.connect() as conn:
        row = (await conn.execute(query)).first()
    return dict(row._mapping) if row else None


async def alerts_to_process(engine: AsyncEngine, since: datetime, limit: int = 200) -> list[int]:
    """Alerts since `since` never matched, or failed fewer than MAX_ATTEMPTS times. Oldest first."""
    r = AlertImpactRun
    query = (select(Alert.id).outerjoin(r, r.alert_id == Alert.id)
             .where(Alert.created_at >= since,
                    or_(r.status.is_(None), (r.status == "failed") & (r.attempts < MAX_ATTEMPTS)))
             .order_by(Alert.created_at).limit(limit))
    async with engine.connect() as conn:
        return list((await conn.execute(query)).scalars())


async def market_tags(engine: AsyncEngine, alert: dict, entity_map: EntityMap) -> list[tuple[str, str]] | None:
    """The market's map tags as (symbol, type), or None when neither it nor any market of its
    event is enriched under this map version (the caller then enriches it now).
    """
    e = MarketEnrichment
    async with engine.connect() as conn:
        own = (await conn.execute(select(e.status).where(
            e.source == alert["source"], e.market_id == alert["market_id"], e.status == "done",
            e.map_version == entity_map.version))).scalar()
    if own:
        symbols = await _tags_of(engine, alert["source"], alert["market_id"], entity_map)
    elif alert.get("event_id"):
        symbols = await sibling_tags(engine, alert["source"], alert["event_id"], entity_map)
    else:
        symbols = None
    if symbols is None:
        return None
    types = {e.symbol: e.category for e in entity_map.entities}
    return [(s, types[s]) for s in symbols if s in types]


async def _tags_of(engine: AsyncEngine, source: str, market_id: str, entity_map: EntityMap) -> list[str]:
    async with engine.connect() as conn:
        return list((await conn.execute(select(MarketEntity.entity_symbol).where(
            MarketEntity.source == source, MarketEntity.market_id == market_id,
            MarketEntity.entity_symbol.in_(entity_map.symbols)))).scalars())


async def event_markets(engine: AsyncEngine, alert: dict) -> list[dict]:
    """The alert's event's active markets (at least the alert's own), for on-demand enrichment."""
    key = alert.get("event_id") or alert["market_id"]
    query = (select(*MARKET_COLUMNS).where(
        Market.source == alert["source"], EVENT_KEY == key,
        or_(Market.status == "active", Market.market_id == alert["market_id"])).order_by(Market.market_id))
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(query)]


async def graph_links(engine: AsyncEngine, symbols: list[str]) -> list[tuple[str, str, float]]:
    """Company graph edges from filings touching any of `symbols`: (company, other, confidence)."""
    if not symbols:
        return []
    r = EntityRelationship
    query = (select(r.entity_symbol, r.related_entity_symbol, r.confidence)
             .where(or_(r.entity_symbol.in_(symbols), r.related_entity_symbol.in_(symbols)), r.source == "filing"))
    async with engine.connect() as conn:
        return [(a, b, float(c or 0)) for a, b, c in await conn.execute(query)]


async def save_impacts(engine: AsyncEngine, alert: dict, impacts: list[Impact]) -> None:
    """Replace this alert's impacts; add a graph_events row per tier-1 company (needs a market URL)."""
    async with engine.begin() as conn:
        await conn.execute(delete(AlertImpact).where(AlertImpact.alert_id == alert["alert_id"]))
        if impacts:
            await conn.execute(pg_insert(AlertImpact).values([
                {"alert_id": alert["alert_id"], "entity_symbol": i.symbol, "tier": i.tier, "matched": i.matched,
                 "relevance": i.relevance} for i in impacts]))
        direct = [i for i in impacts if i.tier == 1]
        if direct and alert.get("url"):
            title = alert.get("event_title") or alert.get("title") or alert["market_id"]
            stmt = pg_insert(GraphEvent).values([
                {"entity_symbol": i.symbol, "source": EVENT_SOURCE, "event_type": EVENT_TYPE, "title": title,
                 "url": alert["url"], "occurred_at": alert["created_at"], "alert_id": alert["alert_id"]}
                for i in direct])
            g = GraphEvent
            await conn.execute(stmt.on_conflict_do_update(  # same company + market page: keep the newest alert
                index_elements=["entity_symbol", "url"],
                set_={"title": stmt.excluded.title, "occurred_at": stmt.excluded.occurred_at,
                      "alert_id": stmt.excluded.alert_id, "event_type": stmt.excluded.event_type},
                where=(g.source == EVENT_SOURCE) & (g.occurred_at <= stmt.excluded.occurred_at)))


async def save_run(engine: AsyncEngine, alert_id: int, status: str, companies: int = 0,
                   error: str | None = None) -> None:
    r = AlertImpactRun
    values = {"status": status, "companies": companies, "error": (error or "")[:500] or None,
              "processed_at": datetime.now(timezone.utc)}
    stmt = pg_insert(r).values(alert_id=alert_id, attempts=1 if status == "failed" else 0, **values)
    stmt = stmt.on_conflict_do_update(index_elements=["alert_id"], set_={
        **values, "attempts": case((stmt.excluded.status == "failed", r.attempts + 1), else_=0)})
    async with engine.begin() as conn:
        await conn.execute(stmt)


# --- reads for the API ------------------------------------------------------------------

async def recent_alerts(engine: AsyncEngine, since: datetime, limit: int, per_alert: int) -> list[dict]:
    """Recent alerts with their market and up to `per_alert` impacts each, newest first."""
    query = (select(Alert.id, Alert.created_at, Alert.source, Alert.market_id, Alert.direction, Alert.summary,
                    Alert.score, Market.title, Market.event_title, Market.url)
             .outerjoin(Market, (Market.source == Alert.source) & (Market.market_id == Alert.market_id))
             .where(Alert.created_at >= since).order_by(Alert.created_at.desc()).limit(limit))
    async with engine.connect() as conn:
        alerts = [dict(r._mapping) for r in await conn.execute(query)]
        if not alerts:
            return []
        impacts = (await conn.execute(
            select(AlertImpact.alert_id, AlertImpact.entity_symbol, AlertImpact.tier, AlertImpact.matched,
                   AlertImpact.relevance, Entity.name)
            .outerjoin(Entity, Entity.symbol == AlertImpact.entity_symbol)
            .where(AlertImpact.alert_id.in_([a["id"] for a in alerts]))
            .order_by(AlertImpact.alert_id, AlertImpact.tier, AlertImpact.relevance.desc().nulls_last(),
                      AlertImpact.entity_symbol))).all()
    by_alert: dict[int, list[dict]] = {}
    for i in impacts:
        rows = by_alert.setdefault(i.alert_id, [])
        if len(rows) < per_alert:
            rows.append({"symbol": i.entity_symbol, "name": i.name, "tier": i.tier, "matched": list(i.matched),
                         "relevance": float(i.relevance) if i.relevance is not None else None})
    return [{**a, "companies": by_alert.get(a["id"], [])} for a in alerts]


async def company_alerts(engine: AsyncEngine, symbol: str, since: datetime, limit: int) -> list[dict]:
    """Alerts that affected this company, newest first, with why."""
    query = (select(Alert.id, Alert.created_at, Alert.source, Alert.market_id, Alert.direction, Alert.summary,
                    Alert.score, Market.title, Market.event_title, Market.url, AlertImpact.tier, AlertImpact.matched,
                    AlertImpact.relevance)
             .join(AlertImpact, AlertImpact.alert_id == Alert.id)
             .outerjoin(Market, (Market.source == Alert.source) & (Market.market_id == Alert.market_id))
             .where(AlertImpact.entity_symbol == symbol, Alert.created_at >= since)
             .order_by(Alert.created_at.desc()).limit(limit))
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(query)]
