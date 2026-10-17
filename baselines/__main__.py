"""Baseline job: compute each market's "normal" from days of history into market_baselines.

Run from the repo root next to the ingestion workers:

    uv run python -m baselines            # keep running: new markets within minutes, refresh every 8 h
    uv run python -m baselines --once     # one pass, then exit
    uv run python -m baselines --force    # recompute every active market now (then keep running)
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

from alert_detector import config
from baselines import compute, db, sources

log = logging.getLogger("baselines")

CONCURRENCY = 4  # markets fetched at once per source, to stay inside the APIs' rate limits


async def compute_one(engine, client, market: dict, token: str | None, cfg) -> str:
    """Fetch history for one market and store its baseline. Returns the method used."""
    now = time.time()
    since = now - cfg.baseline_days * 86400
    source = market["source"]
    if source == "kalshi":
        h = await sources.kalshi_history(client, market, since, now, cfg)
    elif source == "polymarket":
        h = await sources.polymarket_history(client, market, token, since, now, cfg)
    else:
        h = await sources.polymarket_us_history(client, market, cfg)

    sigma, samples = compute.sigma_from_points(h.points, cfg.window_s, h.step_s, h.lookback_s)
    if source == "polymarket_us":
        rows = await db.hourly_rows(engine, source, market["market_id"], since)
        volume, minutes, p99, orders = compute.from_hourly(rows, cfg.window_s)
    else:
        # Volume counts from when trading history starts: a market younger than the
        # window shouldn't look quiet because of days it didn't exist yet.
        first = min([p[0] for p in h.points] + [t.t for t in h.trades], default=now)
        start = max(h.trades_from, first)
        volume = compute.volume_per_window(h.trades, now - start, cfg.window_s)
        minutes = (now - start) / 60
        p99, orders = compute.whale_from_trades(h.trades)
    await db.upsert_baseline(engine, {
        "source": source, "market_id": market["market_id"], "computed_at": datetime.fromtimestamp(now, tz=timezone.utc),
        "method": h.method, "sigma_5m": sigma, "sigma_samples": samples, "volume_per_window": volume,
        "history_minutes": minutes, "whale_p99": p99, "whale_orders": orders,
    })
    return h.method


async def sweep(engine, client, cfg, force: bool) -> None:
    """Compute baselines for markets that have none, or whose baseline is older than the refresh period."""
    older_than = time.time() + 1 if force else time.time() - cfg.baseline_refresh_hours * 3600
    markets = await db.markets_needing_baselines(engine, older_than)
    if not markets:
        return
    started = time.time()
    tokens = {}
    poly_ids = [m["market_id"] for m in markets if m["source"] == "polymarket"]
    if poly_ids:
        tokens = await sources.polymarket_tokens(client, poly_ids)

    done, failed = Counter(), Counter()
    slots = {s: asyncio.Semaphore(CONCURRENCY) for s in ("kalshi", "polymarket", "polymarket_us")}

    async def one(market):
        source = market["source"]
        if source == "polymarket" and market["market_id"] not in tokens:
            failed[source] += 1  # closed since discovery, or not found
            return
        async with slots[source]:
            try:
                await compute_one(engine, client, market, tokens.get(market["market_id"]), cfg)
                done[source] += 1
            except Exception as e:  # one bad market must never stop the sweep
                failed[source] += 1
                log.warning("Baseline failed for %s %s: %s %s", source, market["market_id"],
                            type(e).__name__, e)
        if sum(done.values()) % 100 == 0 and done:
            log.info("... %d of %d done", sum(done.values()), len(markets))

    log.info("Computing baselines for %d markets (%s)", len(markets),
             ", ".join(f"{s} {n}" for s, n in sorted(Counter(m["source"] for m in markets).items())))
    await asyncio.gather(*(one(m) for m in markets))
    log.info("Baselines stored: %s; failed: %s; took %.0fs", dict(done) or 0, dict(failed) or 0,
             time.time() - started)


async def main(once: bool, force: bool) -> None:
    load_dotenv()
    cfg = config.load()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL in .env")
    try:
        engine = await db.connect(url)
    except (OSError, SQLAlchemyError, asyncio.TimeoutError) as e:
        sys.exit(f"Cannot connect to the database: {e}")
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            await sweep(engine, client, cfg, force)
            while not once:
                await asyncio.sleep(cfg.baseline_sweep_min * 60)
                try:
                    await sweep(engine, client, cfg, force=False)
                except (httpx.HTTPError, SQLAlchemyError, OSError) as e:
                    log.warning("Sweep failed, will retry: %s", e)
        except asyncio.CancelledError:
            pass
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m baselines")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--force", action="store_true", help="recompute every active market's baseline now")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(main(args.once, args.force))
    except KeyboardInterrupt:
        pass
