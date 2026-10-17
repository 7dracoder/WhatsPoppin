"""Demo: insert fake markets whose data should (or should not) trigger each kind of alert.

Start the detector first, then in a second terminal:

    python -m alert_detector.demo            # insert scenarios, then report which ones alerted
    python -m alert_detector.demo --source polymarket   # same, as Polymarket markets
    python -m alert_detector.demo --cleanup  # delete all demo markets, rows and alerts

Each run uses fresh tickers (KXDEMO-<time>-<scenario>), so you can rerun it
without hitting the 10-minute cooldown. The KXDEMO series isn't followed by the
ingestion worker, so it never touches these markets.
"""

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from dotenv import load_dotenv
from sqlalchemy import delete, insert, select

from alert_detector import db
from backend.ingestion.common.db import DEMO_SERIES as SERIES  # the workers never close markets in this series
from backend.models import Alert, Market, MarketBaseline, MarketHourly, MarketPrice, MarketTrade

RELOAD_WAIT_S = 65   # the detector reloads market metadata and baselines every 60 s
REPORT_WAIT_S = 20   # how long to wait for alerts after inserting the trigger rows
YES_PRICE = 0.50     # trades at 0.50, so $1 of notional = 2 contracts on either side


@dataclass
class Normal:
    """The fake market_baselines row: what "normal" looks like for the scenario."""
    volume_per_window: float = 50.0   # $ per 5 minutes
    whale_p99: float = 40.0           # $ per order
    whale_orders: int = 600
    sigma_5m: float = 0.01            # typical 5-minute move: 1 point
    sigma_samples: int = 800
    history_minutes: float = 3 * 24 * 60


@dataclass
class Scenario:
    key: str
    title: str
    expect: list[str] | None  # reasons the alert should have; None = must NOT alert
    base_mid: float = 0.50
    jump_to: float | None = None  # mid 20 s before now, if the price moves
    normal: Normal = field(default_factory=Normal)
    window_trades: list = field(default_factory=list)  # (seconds ago, $, side)
    close_in_s: float = 86400


def scenarios() -> list[Scenario]:
    many_yes = [(200 - i * 20, 100, "yes") for i in range(8)]
    busy = Normal(volume_per_window=827)  # orders are usually $40, ~$830 per 5 minutes
    quiet = Normal(volume_per_window=10, whale_orders=30)  # too few orders for a p99: $1,000 whale bar
    return [
        Scenario("PRICE", "Price move: mid jumps 0.40 -> 0.48 where 5-min moves are usually 1 pt", ["price_move"],
                 base_mid=0.40, jump_to=0.48),
        Scenario("WHALE", "Whale: one $2,000 order where orders are usually $40", ["whale"],
                 normal=busy, window_trades=[(20, 2000, "yes")]),
        Scenario("BURST", "Volume burst + imbalance: $820 in 5 min, 98% YES-buying, usually ~$10",
                 ["volume_burst", "imbalance"], normal=quiet, window_trades=many_yes + [(30, 20, "no")]),
        Scenario("ALL", "Everything: price jump, volume burst, whale, one-sided buying",
                 ["price_move", "volume_burst", "whale", "imbalance"], base_mid=0.30, jump_to=0.42,
                 normal=quiet, window_trades=many_yes + [(15, 1500, "yes")]),
        Scenario("CHURN", "Should NOT alert: one-sided buying with no price or volume change", None,
                 normal=busy, window_trades=[(200 - i * 30, 60, "yes") for i in range(6)]),
        Scenario("CLOSING", "Should NOT alert: price jump, but the market closes in 10 min", None,
                 base_mid=0.40, jump_to=0.48, close_in_s=600),
    ]


def _dt(t: float) -> datetime:
    return datetime.fromtimestamp(t, tz=timezone.utc)


def _dec(x: float) -> Decimal:
    return Decimal(str(round(x, 6)))


def _price_row(source: str, market_id: str, t: float, mid: float) -> dict:
    return {"source": source, "market_id": market_id, "timestamp": _dt(t),
            "price_or_odds": _dec(mid), "yes_bid": _dec(mid - 0.01), "yes_ask": _dec(mid + 0.01),
            "yes_bid_size": Decimal(100), "yes_ask_size": Decimal(100), "volume": None,
            "open_interest": None, "snapshot": False}


def _trade_row(source: str, market_id: str, t: float, dollars: float, side: str, n: int) -> dict:
    return {"source": source, "market_id": market_id, "timestamp": _dt(t), "trade_id": f"{market_id}-{n}",
            "yes_price": _dec(YES_PRICE), "count": _dec(dollars / YES_PRICE), "taker_side": side,
            "is_block_trade": False}


def baseline_row(s: Scenario, source: str, market_id: str, now: float) -> dict:
    n = s.normal
    return {"source": source, "market_id": market_id, "computed_at": _dt(now), "method": "demo",
            "sigma_5m": _dec(n.sigma_5m), "sigma_samples": n.sigma_samples,
            "volume_per_window": _dec(n.volume_per_window), "history_minutes": _dec(n.history_minutes),
            "whale_p99": _dec(n.whale_p99), "whale_orders": n.whale_orders}


def build_rows(s: Scenario, source: str, market_id: str, now: float) -> tuple[list[dict], list[dict]]:
    """Only the last few minutes: two steady quotes, then the jump (or one more steady quote)."""
    prices = [_price_row(source, market_id, now - ago, s.base_mid) for ago in (12 * 60, 6 * 60)]
    prices.append(_price_row(source, market_id, now - 20, s.jump_to if s.jump_to is not None else s.base_mid))
    trades = [_trade_row(source, market_id, now - ago, d, side, i)
              for i, (ago, d, side) in enumerate(s.window_trades)]
    return prices, trades


async def run(engine, source: str) -> None:
    run_id = time.strftime("%H%M%S")
    plan = [(s, f"{SERIES}-{run_id}-{s.key}") for s in scenarios()]

    now = time.time()
    async with engine.begin() as conn:
        await conn.execute(insert(Market), [{
            "source": source, "market_id": market_id, "title": f"DEMO: {s.title}", "outcome_label": s.key,
            "rules_primary": "Fake market inserted by alert_detector.demo.", "event_id": market_id,
            "event_title": f"Demo event {s.key}", "series_id": SERIES, "series_title": "Alert detector demo",
            "category": "Demo", "tags": ["demo"], "status": "active", "close_time": _dt(now + s.close_in_s),
        } for s, market_id in plan])
        await conn.execute(insert(MarketBaseline), [baseline_row(s, source, m, now) for s, m in plan])
    print(f"Inserted {len(plan)} demo markets and their baselines. "
          f"Waiting {RELOAD_WAIT_S}s for the detector to load them...")
    await asyncio.sleep(RELOAD_WAIT_S)

    now = time.time()
    async with engine.begin() as conn:
        for s, market_id in plan:
            prices, trades = build_rows(s, source, market_id, now)
            await conn.execute(insert(MarketPrice), prices)
            if trades:
                await conn.execute(insert(MarketTrade), trades)
    print(f"Inserted the last few minutes of quotes and trades. Waiting up to {REPORT_WAIT_S}s for alerts...\n")

    ids = [market_id for _, market_id in plan]
    found: dict[str, list[str]] = {}
    deadline = time.time() + REPORT_WAIT_S
    while time.time() < deadline:
        await asyncio.sleep(2)
        async with engine.connect() as conn:
            rows = await conn.execute(select(Alert.market_id, Alert.reasons)
                                      .where(Alert.source == source, Alert.market_id.in_(ids)))
            found = {r.market_id: list(r.reasons) for r in rows}
        if len(found) >= sum(1 for s, _ in plan if s.expect):
            break

    ok_all = True
    for s, market_id in plan:
        got = found.get(market_id)
        ok = (got is None) if s.expect is None else (got is not None and sorted(got) == sorted(s.expect))
        ok_all &= ok
        expected = "no alert" if s.expect is None else ",".join(s.expect)
        actual = "no alert" if got is None else ",".join(got)
        print(f"{'PASS' if ok else 'FAIL'}  {s.key:<8} expected {expected:<42} got {actual}")
        print(f"      {s.title}")
    print("\nAll scenarios behaved as expected." if ok_all else
          "\nSome scenarios differ. Is `python -m alert_detector` running with default thresholds?")
    print("See them with: SELECT id, reasons, summary FROM alerts WHERE series_id = 'KXDEMO' ORDER BY id;")


async def cleanup(engine) -> None:
    pattern = f"{SERIES}-%"
    async with engine.begin() as conn:
        for model in (Alert, MarketTrade, MarketPrice, MarketHourly, MarketBaseline, Market):
            result = await conn.execute(delete(model).where(model.market_id.like(pattern)))
            print(f"Deleted {result.rowcount} rows from {model.__tablename__}")


async def main(do_cleanup: bool, source: str) -> None:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL in .env")
    engine = await db.connect(url)
    try:
        await (cleanup(engine) if do_cleanup else run(engine, source))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m alert_detector.demo")
    parser.add_argument("--cleanup", action="store_true", help="delete all demo markets, rows and alerts")
    parser.add_argument("--source", choices=["kalshi", "polymarket", "polymarket_us"], default="kalshi",
                        help="which source the fake markets belong to (default kalshi)")
    args = parser.parse_args()
    asyncio.run(main(args.cleanup, args.source))
