"""Polymarket (global) -> Postgres worker. Run from the repo root: uv run python -m ingestion.polymarket"""

import logging
import os
import sys
import time

import httpx
from dotenv import load_dotenv

from backend.ingestion.common import db, runner
from backend.ingestion.polymarket.polymarket import (GAMMA_URL, SOURCE, MarketInfo, MarketSession, fetch_events,
                                             market_rows)

log = logging.getLogger("ingestion.polymarket")


async def main() -> None:
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    tags = [t.strip() for t in os.environ.get("POLYMARKET_TAGS", "").split(",") if t.strip()]
    max_markets = int(os.environ.get("POLYMARKET_MAX_MARKETS") or 500)
    if not (database_url and tags):
        sys.exit("Set DATABASE_URL and POLYMARKET_TAGS in .env")
    http = httpx.AsyncClient(base_url=GAMMA_URL, timeout=30)
    first_discovery = True

    async def discover(engine) -> dict[str, MarketInfo]:
        """Fetch open markets under the tags, store the top N, return them with their tokens."""
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
        return {r["market_id"]: r["info"] for r in rows}

    def make_session(engine, writer):
        return MarketSession(lambda: discover(engine), writer.add_price, writer.add_trade,
                             lambda market_id: db.close_markets(engine, SOURCE, [market_id]))

    await runner.run_worker(log, database_url, make_session, cleanup=http.aclose)


if __name__ == "__main__":
    runner.start(main)
