"""Create every What'sPoppin table on a fresh database. Safe to re-run.

    uv run python scripts/init_db.py

The API only creates the few tables it owns (projects, caches); the rest are normally created
by the workers on their first run. Running this once means every route answers on an empty
database (empty lists instead of "relation does not exist" errors) before any worker has run.
"""

import asyncio
import importlib
import os
import pkgutil
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere

import backend.models  # noqa: E402
from backend.alert_impact.db import add_columns  # noqa: E402
from backend.enrichment.db import sync_entity_map  # noqa: E402
from backend.entities import load_entity_map  # noqa: E402
from backend.ingestion.common.db import connect  # noqa: E402


def register_all_models() -> None:
    """Import every module in backend/models and company_graph/db so all tables are on Base.metadata."""
    for mod in pkgutil.iter_modules(backend.models.__path__):
        importlib.import_module(f"backend.models.{mod.name}")
    importlib.import_module("company_graph.db")


async def main() -> None:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL in .env first.")
    register_all_models()
    engine = await connect(url)  # create_all over the shared metadata
    try:
        await add_columns(engine)
        await sync_entity_map(engine, load_entity_map())  # seed companies + market_entities indexes
    finally:
        await engine.dispose()
    print("Database ready.")


if __name__ == "__main__":
    asyncio.run(main())
