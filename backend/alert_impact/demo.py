"""Mock live moves for a demo: alerts on real markets, already matched to the companies they are
tagged with, so the workspace's live list, bell and node pulse have something to show without the
detector, the impact worker or any Jev call. Run from the repo root:

    uv run python -m backend.alert_impact.demo                 # 8 moves on markets tagged to the example tickers
    uv run python -m backend.alert_impact.demo --count 12 --tickers NVDA AMD
    uv run python -m backend.alert_impact.demo --cleanup       # remove every demo alert

Demo alerts are marked in `alerts.context` ({"demo": true}) and get a done `alert_impact_runs`
row, so a running impact worker leaves them alone and --cleanup finds them.
"""

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from dotenv import load_dotenv
from sqlalchemy import delete, insert, select, text

from backend.ingestion.common.db import make_engine
from backend.models import Alert, Market
from backend.models.alert_impact import AlertImpact
from backend.models.alert_impact_run import AlertImpactRun
from backend.models.entity import Entity
from backend.models.market_entity import MarketEntity

EXAMPLE_TICKERS = ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN"]
VENUE = {"kalshi": "Kalshi", "polymarket": "Polymarket", "polymarket_us": "Polymarket US"}


async def candidate_markets(conn, tickers: list[str], limit: int) -> list[dict]:
    """Active markets tagged with one of `tickers` (one row per market, with all its company tags)."""
    me, m, e = MarketEntity, Market, Entity
    rows = (await conn.execute(
        select(m.source, m.market_id, m.title, m.event_title, m.event_id, m.series_id, me.entity_symbol)
        .join(me, (me.source == m.source) & (me.market_id == m.market_id))
        .join(e, e.symbol == me.entity_symbol)
        .where(m.status == "active", e.type == "company", me.entity_symbol.in_(tickers))
        .order_by(m.market_id))).all()
    by_market: dict[tuple, dict] = {}
    for r in rows:
        key = (r.source, r.market_id)
        entry = by_market.setdefault(key, {**dict(r._mapping), "companies": []})
        entry["companies"].append(r.entity_symbol)
    markets = list(by_market.values())
    random.shuffle(markets)
    return markets[:limit]


def alert_row(m: dict, now: datetime, i: int) -> dict:
    rng = random.Random(f"{m['market_id']}-{i}")
    direction = rng.choice(["yes_up", "yes_down"])
    change = round(rng.uniform(0.06, 0.18), 4) * (1 if direction == "yes_up" else -1)
    before = round(rng.uniform(0.25, 0.65), 4)
    notional = round(rng.uniform(1500, 9000), 0)
    ratio = round(rng.uniform(6, 30), 1)
    score = round(min(abs(change) / 0.05, 3) + min(ratio / 5, 3) + (1 if rng.random() < 0.4 else 0), 4)
    created = now - timedelta(minutes=rng.randint(1, 50))
    title = m.get("event_title") or m.get("title") or m["market_id"]
    pts = abs(change) * 100
    summary = (f"[{VENUE.get(m['source'], m['source'])}] {title} (Yes): "
               f"{'up' if change > 0 else 'down'} {pts:.0f} pts to {before + change:.2f} in 5 min; "
               f"${notional / 1000:.1f}k traded ({ratio}x normal). [demo]")
    return {
        "created_at": created, "status": "done", "source": m["source"], "market_id": m["market_id"],
        "event_id": m.get("event_id"), "series_id": m.get("series_id"), "direction": direction,
        "reasons": ["price_move", "volume_burst"], "score": Decimal(str(score)),
        "window_start": created - timedelta(minutes=5), "window_end": created,
        "mid_before": Decimal(str(before)), "mid_now": Decimal(str(round(before + change, 4))),
        "change_pts": Decimal(str(change)), "volume_ratio": Decimal(str(ratio)),
        "window_notional": Decimal(str(notional)), "summary": summary,
        "context": {"demo": True, "title": title, "companies": m["companies"]},
    }


async def run(engine, tickers: list[str], count: int) -> None:
    now = datetime.now(timezone.utc)
    async with engine.begin() as conn:
        markets = await candidate_markets(conn, tickers, count)
        if not markets:
            sys.exit(f"No active markets are tagged with {', '.join(tickers)}; run the enrichment worker first")
        for i, m in enumerate(markets):
            alert_id = (await conn.execute(insert(Alert).values(**alert_row(m, now, i)).returning(Alert.id))).scalar()
            await conn.execute(insert(AlertImpact).values([
                {"alert_id": alert_id, "entity_symbol": s, "tier": 1, "matched": [s]} for s in m["companies"]]))
            await conn.execute(insert(AlertImpactRun).values(
                alert_id=alert_id, status="done", attempts=0, companies=len(m["companies"]), processed_at=now))
            print(f"alert {alert_id}: {(m.get('event_title') or m.get('title') or m['market_id'])[:60]} -> "
                  f"{', '.join(m['companies'])}")
    print(f"{len(markets)} demo moves inserted; the workspace shows them for the next hour")


async def cleanup(engine) -> None:
    async with engine.begin() as conn:
        ids = list((await conn.execute(text("select id from alerts where context->>'demo' = 'true'"))).scalars())
        if ids:
            await conn.execute(delete(AlertImpact).where(AlertImpact.alert_id.in_(ids)))
            await conn.execute(delete(AlertImpactRun).where(AlertImpactRun.alert_id.in_(ids)))
            await conn.execute(delete(Alert).where(Alert.id.in_(ids)))
    print(f"removed {len(ids)} demo alerts")


async def main(args) -> None:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL in .env")
    engine = make_engine(url)
    try:
        if args.cleanup:
            await cleanup(engine)
        else:
            await run(engine, [t.upper() for t in args.tickers], args.count)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m backend.alert_impact.demo")
    parser.add_argument("--tickers", nargs="*", default=EXAMPLE_TICKERS, help="companies the moves should touch")
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--cleanup", action="store_true", help="delete every demo alert")
    asyncio.run(main(parser.parse_args()))
