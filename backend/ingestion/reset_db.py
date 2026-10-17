"""Drop and recreate the market tables (there are no migrations). Run: python -m ingestion.reset_db

Deletes ALL rows in alerts, market_trades, market_prices and markets. Prices and
trades only keep 3 hours anyway, and the workers rediscover markets on startup.
Stop the workers and the detector first.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import text

from backend.ingestion.common.db import make_engine
from backend.models import Base

TABLES = ["alerts", "market_trades", "market_prices", "markets"]  # children before parents


async def main() -> None:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL in .env")
    answer = input(f"This deletes all rows in {', '.join(TABLES)}. Type 'yes' to continue: ")
    if answer.strip().lower() != "yes":
        sys.exit("Cancelled; nothing changed.")
    engine = make_engine(url)
    async with engine.begin() as conn:
        for table in TABLES:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print(f"Recreated {', '.join(TABLES)} from the models.")


if __name__ == "__main__":
    asyncio.run(main())
