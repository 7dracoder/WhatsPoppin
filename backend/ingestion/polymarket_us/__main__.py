"""Polymarket US -> Postgres worker. Run from the repo root: uv run python -m ingestion.polymarket_us"""

import logging
import os
import sys
import time

import httpx
from dotenv import load_dotenv

from backend.ingestion.common import db, runner
from backend.ingestion.polymarket_us.polymarket_us import (GATEWAY_URL, SOURCE, MarketSession, fetch_events,
                                                   load_secret, market_rows)

log = logging.getLogger("ingestion.polymarket_us")


async def main() -> None:
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    key_id = os.environ.get("POLYMARKET_US_KEY_ID")
    secret = os.environ.get("POLYMARKET_US_SECRET_KEY")
    tags = [t.strip() for t in (os.environ.get("POLYMARKET_US_TAGS") or "politics,tech,crypto").split(",")
            if t.strip()]
    max_markets = int(os.environ.get("POLYMARKET_US_MAX_MARKETS") or 200)
    if not (database_url and key_id and secret):
        sys.exit("Set DATABASE_URL, POLYMARKET_US_KEY_ID and POLYMARKET_US_SECRET_KEY in .env")
    private_key = load_secret(secret)
    http = httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30)
    first_discovery = True

    async def discover(engine) -> set[str]:
        """Fetch open markets under the tags, store the first N, return their slugs."""
        nonlocal first_discovery
        events = []
        for tag in tags:
            events += await fetch_events(http, tag)
        rows = market_rows(events, tags, max_markets, time.time())
        await db.upsert_markets(engine, SOURCE, rows)
        await db.close_missing(engine, SOURCE, None, [r["market_id"] for r in rows])
        if first_discovery:
            log.info("Following %d markets across tags %s", len(rows), ", ".join(tags))
            first_discovery = False
        return {r["market_id"] for r in rows}

    def make_session(engine, writer):
        return MarketSession(key_id, private_key, lambda: discover(engine), writer.add_price, writer.add_trade)

    await runner.run_worker(log, database_url, make_session, cleanup=http.aclose)


if __name__ == "__main__":
    runner.start(main)
