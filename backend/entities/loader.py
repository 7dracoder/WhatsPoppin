"""Loads the seed EntityAlias list for matcher.py.

data/sp500_top50.json is a representative snapshot of ~50 large S&P 500
companies and their common aliases — not a live, verified-today ranking.
Good enough to seed a demo's watchlist; swap in a different/larger file
(or a DB-backed loader) later without changing matcher.py at all, since
EntityMatcher only cares about the EntityAlias list it's handed.
"""

import json
from pathlib import Path

from .models import EntityAlias

DEFAULT_ENTITIES_PATH = Path(__file__).parent / "data" / "sp500_top50.json"


def load_entities(path: Path = DEFAULT_ENTITIES_PATH) -> list[EntityAlias]:
    """Read a JSON file of [{"symbol", "name", "aliases"}, ...] into
    EntityAlias objects. Call this once at startup and pass the result into
    whatever ingestion source's poll/tag function needs it — there's no
    need to re-read the file per poll.
    """
    raw = json.loads(path.read_text())
    return [EntityAlias(symbol=entry["symbol"], aliases=entry["aliases"]) for entry in raw]
