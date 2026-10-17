"""The predefined entity map that market enrichment (backend/enrichment) picks from.

data/entity_map.json holds `map_version` and the names in five categories. Companies
come from the file it names in `companies_file` (sp500_top50.json, the same list the
matcher and the company graph use), so there is one company list, not two. Bump
`map_version` when either file changes: the enrichment worker then re-enriches every
market enriched under an older version.

Each entry becomes an `entities` row: `symbol` is the ticker for a company and the name
itself for everything else (db-design.md's convention, e.g. "Trump", "Semiconductors"),
and `type` is the category.
"""

import json
from pathlib import Path

from pydantic import BaseModel

DEFAULT_MAP_PATH = Path(__file__).parent / "data" / "entity_map.json"

# Category -> what it covers, worded for Jev's category pass.
CATEGORIES: dict[str, str] = {
    "company": "a specific publicly traded company",
    "country": "a specific country, its government, economy or policy, or a place in it such as a city, "
               "state or region",
    "sector": "an industry or sector of the economy",
    "event": "an event such as an election, economic data release, central bank decision, "
             "war, disaster, regulation, or corporate event like earnings or a merger",
    "resource": "a commodity, natural resource, currency or cryptocurrency",
    "person": "a specific named person",
}


class MapEntity(BaseModel):
    symbol: str  # entities.symbol
    name: str
    category: str  # one of CATEGORIES; stored as entities.type
    aliases: list[str] = []  # companies only, from the companies file


class EntityMap(BaseModel):
    version: int
    entities: list[MapEntity]

    def in_category(self, category: str) -> list[MapEntity]:
        return [e for e in self.entities if e.category == category]

    @property
    def symbols(self) -> list[str]:
        return [e.symbol for e in self.entities]


def load_entity_map(path: Path = DEFAULT_MAP_PATH) -> EntityMap:
    """Read and validate the map. Raises ValueError on an unknown category, a duplicate
    symbol or name (Jev answers by name), or a "/" in a symbol (it is a URL path segment).
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    companies = json.loads((path.parent / raw["companies_file"]).read_text(encoding="utf-8"))
    entities = [MapEntity(symbol=c["symbol"], name=c["name"], category="company", aliases=c["aliases"])
                for c in companies]
    for category, names in raw["categories"].items():
        if category not in CATEGORIES or category == "company":
            raise ValueError(f"Unknown category in {path.name}: {category!r}")
        entities += [MapEntity(symbol=name, name=name, category=category) for name in names]

    for field in ("symbol", "name"):
        seen: set[str] = set()
        for e in entities:
            key = getattr(e, field).casefold()
            if key in seen:
                raise ValueError(f"Duplicate entity {field} in the map: {getattr(e, field)!r}")
            seen.add(key)
    bad = [e.symbol for e in entities if "/" in e.symbol]
    if bad:
        raise ValueError(f"Entity symbols cannot contain '/': {bad}")
    return EntityMap(version=raw["map_version"], entities=entities)
