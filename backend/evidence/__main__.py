"""Pre-fetch news for the demo tickers so /evidence/news answers from the cache (no metered call,
no dependence on NewsAPI being up during a demo). Run from the repo root:

    uv run python -m backend.evidence                      # the example tickers, last 30 days
    uv run python -m backend.evidence NVDA AMD --days 14   # specific tickers / window

NewsAPI's free plan reaches back one month, so --days above 30 is rejected upstream.
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.evidence.news import news_for
from backend.evidence.news_cache import PostgresNewsStore
from backend.ingestion.common.db import make_engine
from backend.ingestion.news_api import NewsApiError, NewsApiGateway
from backend.models.base import Base
from backend.models.news_cache import NewsCache

# The chips under the workspace's ticker box (frontend TickerInput.tsx RECOMMENDED_TICKERS).
EXAMPLE_TICKERS = ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN"]
log = logging.getLogger("prefetch_news")


async def main(tickers: list[str], days: int) -> int:
    load_dotenv()
    url, key = os.environ.get("DATABASE_URL"), os.environ.get("NEWSAPI_KEY")
    if not url or not key:
        sys.exit("Set DATABASE_URL and NEWSAPI_KEY in .env")
    engine = make_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[NewsCache.__table__])
    store = PostgresNewsStore(async_sessionmaker(engine, expire_on_commit=False))
    gateway = NewsApiGateway(api_key=key)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    failures = 0
    for ticker in tickers:
        try:
            items, origin = await news_for(ticker.upper(), start, end, gateway, store)
            log.info("%s: %d articles (%s)", ticker.upper(), len(items), origin)
        except NewsApiError as exc:
            failures += 1
            log.error("%s: %s", ticker.upper(), exc)
    await engine.dispose()
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m backend.evidence")
    parser.add_argument("tickers", nargs="*", default=EXAMPLE_TICKERS)
    parser.add_argument("--days", type=int, default=30)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parser.parse_args()
    sys.exit(1 if asyncio.run(main(args.tickers, args.days)) else 0)
