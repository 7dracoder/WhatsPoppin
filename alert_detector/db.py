"""Postgres via SQLAlchemy: table setup, initial load, incremental polling, alert insert + notify."""

from datetime import datetime, timezone

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.ingestion.common.db import connect  # same engine setup; creates any missing tables (incl. alerts)
from backend.models import Alert, Market, MarketBaseline, MarketPrice, MarketTrade

__all__ = ["connect", "load_initial", "poll_new", "load_markets", "load_baselines", "recent_alerts",
           "insert_alert"]

PAGE = 20_000
PRICE_FIELDS = (MarketPrice.id, MarketPrice.source, MarketPrice.market_id, MarketPrice.timestamp, MarketPrice.yes_bid,
                MarketPrice.yes_ask, MarketPrice.yes_bid_size, MarketPrice.yes_ask_size, MarketPrice.snapshot)
TRADE_FIELDS = (MarketTrade.id, MarketTrade.source, MarketTrade.market_id, MarketTrade.timestamp, MarketTrade.yes_price,
                MarketTrade.count, MarketTrade.taker_side, MarketTrade.is_block_trade)


async def load_initial(engine: AsyncEngine, state, since: float) -> None:
    """Fill the state with the last few hours of rows, in id (= arrival) order."""
    since_dt = datetime.fromtimestamp(since, tz=timezone.utc)
    async with engine.connect() as conn:
        # Fix the cut-off ids first: rows inserted while we load are left for poll_new.
        # Rows older than `since` are skipped but still count as already seen.
        max_price = (await conn.execute(text("SELECT coalesce(max(id), 0) FROM market_prices"))).scalar()
        max_trade = (await conn.execute(text("SELECT coalesce(max(id), 0) FROM market_trades"))).scalar()
        for row in await conn.execute(select(*PRICE_FIELDS).where(MarketPrice.timestamp > since_dt,
                                                                  MarketPrice.id <= max_price)
                                      .order_by(MarketPrice.id)):
            state.add_price(row)
        for row in await conn.execute(select(*TRADE_FIELDS).where(MarketTrade.timestamp > since_dt,
                                                                  MarketTrade.id <= max_trade)
                                      .order_by(MarketTrade.id)):
            state.add_trade(row)
    state.last_price_id, state.last_trade_id = max_price, max_trade


async def poll_new(engine: AsyncEngine, state) -> set[tuple]:
    """Read rows added since the last poll; return the (source, market_id)s that got new data.

    `id > last seen id` is safe because the ingestion worker is the only writer
    and inserts one batch at a time, so ids become visible in order.
    """
    changed = set()
    async with engine.connect() as conn:
        for fields, model, attr, add in ((PRICE_FIELDS, MarketPrice, "last_price_id", state.add_price),
                                         (TRADE_FIELDS, MarketTrade, "last_trade_id", state.add_trade)):
            while True:
                rows = (await conn.execute(select(*fields).where(model.id > getattr(state, attr))
                                           .order_by(model.id).limit(PAGE))).all()
                for row in rows:
                    changed.add(add(row))
                if len(rows) < PAGE:
                    break
    return changed


async def load_markets(engine: AsyncEngine) -> dict[tuple, dict]:
    async with engine.connect() as conn:
        rows = await conn.execute(select(Market).where(Market.status == "active"))
        return {(r.source, r.market_id): dict(r._mapping) for r in rows}


async def load_baselines(engine: AsyncEngine) -> dict[tuple, object]:
    """market_baselines rows (written by python -m baselines), by (source, market_id)."""
    async with engine.connect() as conn:
        rows = await conn.execute(select(MarketBaseline))
        return {(r.source, r.market_id): r for r in rows}


async def recent_alerts(engine: AsyncEngine, seconds: float) -> list[tuple[str, float, float]]:
    """(event key, created_at, score) of recent alerts, to seed the cooldown after a restart."""
    since = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - seconds, tz=timezone.utc)
    async with engine.connect() as conn:
        rows = await conn.execute(select(Alert.source, Alert.event_id, Alert.market_id,
                                         Alert.created_at, Alert.score)
                                  .where(Alert.created_at > since).order_by(Alert.id))
        return [(f"{r.source}:{r.event_id or r.market_id}", r.created_at.timestamp(), float(r.score))
                for r in rows]


async def insert_alert(engine: AsyncEngine, alert: dict) -> int:
    async with engine.begin() as conn:
        alert_id = (await conn.execute(insert(Alert).values(**alert).returning(Alert.id))).scalar_one()
        # Lets the enricher LISTEN for new alerts instead of polling.
        await conn.execute(text("SELECT pg_notify('alerts', :id)"), {"id": str(alert_id)})
    return alert_id
