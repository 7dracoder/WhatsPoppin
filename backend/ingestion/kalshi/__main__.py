"""Kalshi -> Postgres worker. Run from the repo root: uv run python -m ingestion.kalshi"""

import logging
import os
import sys
import time

import httpx
from dotenv import load_dotenv

from backend.ingestion.common import db, runner
from backend.ingestion.kalshi.kalshi import (REST_URL, MarketSession, discover_categories, discover_series,
                                             load_private_key, top_by_volume)

log = logging.getLogger("ingestion.kalshi")
SOURCE = "kalshi"


async def main() -> None:
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    key_id = os.environ.get("KALSHI_API_KEY_ID")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH") or "kalshi_key.pem"
    series = [s.strip() for s in os.environ.get("KALSHI_SERIES", "").split(",") if s.strip()]
    categories = [c.strip() for c in os.environ.get("KALSHI_CATEGORIES", "").split(",") if c.strip()]
    max_markets = int(os.environ.get("KALSHI_MAX_MARKETS") or 1000)
    if not (database_url and key_id and (series or categories)):
        sys.exit("Set DATABASE_URL, KALSHI_API_KEY_ID and KALSHI_CATEGORIES or KALSHI_SERIES in .env")
    private_key = load_private_key(key_path)
    http = httpx.AsyncClient(base_url=REST_URL, timeout=15)
    first_discovery = True

    async def discover(engine) -> set[str]:
        """Fetch open markets, store them all (search sees every one), return the tickers to stream."""
        nonlocal first_discovery
        if categories:
            rows = await discover_categories(http, categories, series)
        else:
            rows = []
            for s in series:
                rows += await discover_series(http, s)
        await db.upsert_markets(engine, SOURCE, rows)
        # Category mode owns every Kalshi market, so anything no longer listed is closed.
        await db.close_missing(engine, SOURCE, None if categories else series, [r["market_id"] for r in rows])
        followed = top_by_volume(rows, max_markets, time.time())
        if first_discovery:
            log.info("Stored %d open markets across %s; streaming the top %d by 24h volume",
                     len(rows), ", ".join(categories + series), len(followed))
            first_discovery = False
        return followed

    def make_session(engine, writer):
        return MarketSession(key_id, private_key, lambda: discover(engine), writer.add_price, writer.add_trade)

    await runner.run_worker(log, database_url, make_session, cleanup=http.aclose)


if __name__ == "__main__":
    runner.start(main)
