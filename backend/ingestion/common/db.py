"""Postgres via SQLAlchemy, shared by every source: setup, market upserts, batched writer, retention."""

import logging
from collections import deque
from urllib.parse import parse_qs, urlencode

from sqlalchemy import delete, func, insert, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from backend.models import Base, Market, MarketHourly, MarketPrice, MarketTrade

log = logging.getLogger(__name__)

MAX_QUEUE = 100_000  # per table
UPSERT_CHUNK = 1000  # keeps each statement under Postgres' bind-parameter limit
RETENTION = "30 minutes"  # live rows; "normal" comes from market_baselines (python -m baselines)
HOURLY_RETENTION = "14 days"
CLOSED_RETENTION = HOURLY_RETENTION  # closed markets outlive their market_hourly rows, then go
DEMO_SERIES = "KXDEMO"  # fake markets from alert_detector.demo; workers never close them
PRICE_COLUMNS = ["source", "market_id", "timestamp", "price_or_odds", "yes_bid", "yes_ask",
                 "yes_bid_size", "yes_ask_size", "volume", "open_interest", "snapshot"]
TRADE_COLUMNS = ["source", "market_id", "timestamp", "trade_id", "yes_price", "count",
                 "taker_side", "is_block_trade"]
MARKET_FIELDS = ["title", "outcome_label", "rules_primary", "event_id", "event_title",
                 "series_id", "series_title", "category", "tags", "close_time", "url"]


def _async_url(url: str) -> str:
    """Accept a plain postgresql:// URL; SQLAlchemy needs the asyncpg driver named.
    Also strips libpq-style query params (sslmode, channel_binding) that hosted
    providers like Neon append but asyncpg's connect() doesn't accept — SSL is
    requested separately via connect_args instead (see make_engine).
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix):]
            break
    base, _, query = url.partition("?")
    if not query:
        return base
    params = parse_qs(query)
    params.pop("sslmode", None)
    params.pop("channel_binding", None)
    return base + ("?" + urlencode(params, doseq=True) if params else "")


def make_engine(url: str) -> AsyncEngine:
    connect_args = {"timeout": 10}
    if "sslmode=require" in url or "sslmode=verify-full" in url:
        connect_args["ssl"] = True
    return create_async_engine(_async_url(url), pool_size=3, max_overflow=0,
                               connect_args=connect_args)


async def connect(url: str) -> AsyncEngine:
    """Create the engine and the tables (no migrations: tables come from the models)."""
    engine = make_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


async def upsert_markets(engine: AsyncEngine, source: str, rows: list[dict]) -> None:
    async with engine.begin() as conn:
        for i in range(0, len(rows), UPSERT_CHUNK):
            stmt = pg_insert(Market).values([
                {"source": source, "market_id": r["market_id"], "status": "active",
                 **{f: r.get(f) for f in MARKET_FIELDS}}
                for r in rows[i:i + UPSERT_CHUNK]
            ])
            stmt = stmt.on_conflict_do_update(
                index_elements=["source", "market_id"],
                set_={**{f: stmt.excluded[f] for f in MARKET_FIELDS},
                      "status": "active", "updated_at": func.now()},
            )
            await conn.execute(stmt)


async def close_missing(engine: AsyncEngine, source: str, group_ids: list[str] | None,
                        open_ids: list[str]) -> None:
    """Active markets of this source (and these series, if given) no longer in the open list -> closed."""
    conditions = [Market.source == source, Market.status == "active", Market.market_id.not_in(open_ids),
                  Market.series_id.is_distinct_from(DEMO_SERIES)]
    if group_ids is not None:
        conditions.append(Market.series_id.in_(group_ids))
    async with engine.begin() as conn:
        await conn.execute(update(Market).where(*conditions).values(status="closed", updated_at=func.now()))


async def close_markets(engine: AsyncEngine, source: str, market_ids: list[str]) -> None:
    async with engine.begin() as conn:
        await conn.execute(update(Market)
                           .where(Market.source == source, Market.market_id.in_(market_ids))
                           .values(status="closed", updated_at=func.now()))


# Each statement deletes expired rows and adds them to market_hourly in one go, so
# every row is counted exactly once, even when several workers run this at once.
_ROLL_UP_TRADES = text(f"""
WITH gone AS (
  DELETE FROM market_trades WHERE timestamp < now() - interval '{RETENTION}'
  RETURNING source, market_id, timestamp, taker_side,
            count * CASE WHEN taker_side = 'yes' THEN yes_price ELSE 1 - yes_price END AS notional
), orders AS (  -- fills with the same time and side are one order (same rule as the detector)
  SELECT source, market_id, date_trunc('hour', timestamp) AS hour, sum(notional) AS notional
  FROM gone GROUP BY source, market_id, timestamp, taker_side
)
INSERT INTO market_hourly AS h (source, market_id, hour, notional, orders, order_sizes)
SELECT source, market_id, hour, sum(notional), count(*), array_agg(notional)
FROM orders GROUP BY source, market_id, hour
ON CONFLICT (source, market_id, hour) DO UPDATE SET
  notional = h.notional + EXCLUDED.notional,
  orders = h.orders + EXCLUDED.orders,
  order_sizes = h.order_sizes || EXCLUDED.order_sizes
""")
_ROLL_UP_PRICES = text(f"""
WITH gone AS (
  DELETE FROM market_prices WHERE timestamp < now() - interval '{RETENTION}'
  RETURNING source, market_id, timestamp, snapshot
)
INSERT INTO market_hourly AS h (source, market_id, hour, quote_rows)
SELECT source, market_id, date_trunc('hour', timestamp), count(*)
FROM gone WHERE NOT snapshot  -- snapshot timestamps can be hours old
GROUP BY source, market_id, date_trunc('hour', timestamp)
ON CONFLICT (source, market_id, hour) DO UPDATE SET quote_rows = h.quote_rows + EXCLUDED.quote_rows
""")


# Markets closed longer than CLOSED_RETENTION, with their baselines. Markets an alert
# points at are kept, and so is any market still holding live rows (the foreign keys).
_DELETE_CLOSED_MARKETS = text(f"""
WITH gone AS (
  DELETE FROM markets m
  WHERE m.status = 'closed' AND m.updated_at < now() - interval '{CLOSED_RETENTION}'
    AND m.series_id IS DISTINCT FROM '{DEMO_SERIES}'
    AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.source = m.source AND a.market_id = m.market_id)
    AND NOT EXISTS (SELECT 1 FROM market_prices p WHERE p.source = m.source AND p.market_id = m.market_id)
    AND NOT EXISTS (SELECT 1 FROM market_trades t WHERE t.source = m.source AND t.market_id = m.market_id)
  RETURNING source, market_id
)
DELETE FROM market_baselines b USING gone g
WHERE b.source = g.source AND b.market_id = g.market_id
""")


async def delete_old_rows(engine: AsyncEngine) -> None:
    """Retention: roll expired live rows up into market_hourly, drop old hourly rows and long-closed markets."""
    async with engine.begin() as conn:
        await conn.execute(_ROLL_UP_TRADES)
        await conn.execute(_ROLL_UP_PRICES)
        await conn.execute(delete(MarketHourly).where(
            MarketHourly.hour < text(f"now() - interval '{HOURLY_RETENTION}'")))
    # Own transaction: a failure here must not undo the roll-up above.
    async with engine.begin() as conn:
        await conn.execute(_DELETE_CLOSED_MARKETS)


class _Queue:
    """In-memory rows for one table, inserted in batches."""

    def __init__(self, model, columns: list[str]):
        self.model = model
        self.columns = columns
        self.rows: deque = deque(maxlen=MAX_QUEUE)  # full -> oldest rows drop off
        self.written = 0  # since last stats log

    async def flush(self, engine: AsyncEngine) -> None:
        if not self.rows:
            return
        batch = list(self.rows)
        self.rows.clear()
        try:
            async with engine.begin() as conn:
                await conn.execute(insert(self.model), [dict(zip(self.columns, row)) for row in batch])
            self.written += len(batch)
        except (SQLAlchemyError, OSError) as e:
            log.warning("Insert of %d %s rows failed, will retry: %s",
                        len(batch), self.model.__tablename__, e)
            # Put the batch back in front of rows that arrived meanwhile;
            # the deque's maxlen drops the oldest if this overflows.
            newer = list(self.rows)
            self.rows.clear()
            self.rows.extend(batch)
            self.rows.extend(newer)


class RowWriter:
    """Queues price and trade rows in memory and inserts them in batches."""

    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.prices = _Queue(MarketPrice, PRICE_COLUMNS)
        self.trades = _Queue(MarketTrade, TRADE_COLUMNS)

    def add_price(self, row: tuple) -> None:
        self.prices.rows.append(row)

    def add_trade(self, row: tuple) -> None:
        self.trades.rows.append(row)

    async def flush(self) -> None:
        await self.prices.flush(self.engine)
        await self.trades.flush(self.engine)
