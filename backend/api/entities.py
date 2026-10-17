"""`/entities` router: autocomplete over the entity map, every market linked to one entity, and a
company's top markets (new_specs/top-markets-helper.md).

Mounted in backend/main.py. Read-only: the enrichment worker creates and fills the tables.
"""

import asyncio
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.api.deps import get_db_engine
from backend.api.market_activity import only_traded
from backend.classification import JevError
from backend.enrichment import db
from backend.enrichment.rerank import rerank
from backend.enrichment.tag_match import match_tags
from backend.entities import load_entity_map

router = APIRouter(prefix="/entities", tags=["entities"])

# Without rerank, this many times `limit` are read from the database so that dropping the
# untraded ones (most of a long-dated Kalshi series never trades) still fills the list.
TRADED_OVERSAMPLE = 3

Category = Literal["company", "country", "sector", "event", "resource", "person"]  # CATEGORIES's keys

_MAP = load_entity_map()
_MAP_SYMBOLS = _MAP.symbols


class EntityOut(BaseModel):
    id: str  # entities.symbol: a ticker for companies, the name for everything else
    name: str
    category: str


class MarketOut(BaseModel):
    source: str  # 'kalshi' | 'polymarket' | 'polymarket_us'
    market_id: str
    title: str | None
    outcome_label: str | None
    event_title: str | None
    status: str  # 'active' | 'closed'
    close_time: datetime | None
    url: str | None


class EntityMarketsOut(BaseModel):
    entity: EntityOut
    markets: list[MarketOut]


def _entity(row: dict) -> EntityOut:
    return EntityOut(id=row["symbol"], name=row["name"], category=row["type"])


@router.get("/autocomplete", response_model=list[EntityOut])
async def autocomplete(q: str = Query(min_length=1, max_length=100), category: Category | None = None,
                       limit: int = Query(10, ge=1, le=50), engine: AsyncEngine = Depends(get_db_engine)):
    """Map entities whose name or ticker starts with `q` ("Tes" -> Tesla)."""
    rows = await db.autocomplete(engine, q.strip(), _MAP_SYMBOLS, category, limit)
    return [_entity(r) for r in rows]


@router.get("/{entity_id}/markets", response_model=EntityMarketsOut)
async def entity_markets(entity_id: str, limit: int = Query(200, ge=1, le=1000),
                         engine: AsyncEngine = Depends(get_db_engine)):
    """Every market linked to this entity across Kalshi, Polymarket and Polymarket US."""
    entity = await db.get_entity(engine, entity_id)
    if entity is None:
        raise HTTPException(404, f"Unknown entity: {entity_id}")
    markets = await db.markets_for_entity(engine, entity_id, limit)
    return EntityMarketsOut(entity=_entity(entity), markets=[MarketOut(**m) for m in markets])


class TagOut(BaseModel):
    input: str | None  # the caller's tag, or None for a company-graph link
    id: str
    name: str
    category: str
    source: str  # 'tag' (matched from the caller's tag) | 'graph' (company graph link)


class TopMarketOut(BaseModel):
    source: str
    market_id: str
    event_id: str | None
    title: str | None
    outcome_label: str | None
    event_title: str | None
    rules_primary: str | None = None  # the venue's resolution rule: what YES actually means
    close_time: datetime | None
    url: str | None
    tier: int  # 1 = tagged with the company, 2 = a linked company, 3 = one of the tags
    matched: list[str]  # the entities that made it match
    relevance: float | None = None  # Jev's probability that it matters to the company (rerank only)


class TopMarketsOut(BaseModel):
    company: EntityOut
    tags: list[TagOut]
    unmatched_tags: list[str]  # tags nothing in the entity map fits
    markets: list[TopMarketOut]


@router.get("/{company_id}/top-markets", response_model=TopMarketsOut)
async def top_markets(company_id: str, tags: list[str] = Query([], max_length=20),
                      limit: int = Query(3, ge=1, le=10), rerank_: bool = Query(False, alias="rerank"),
                      candidates: int = Query(50, ge=1, le=200), traded: bool = Query(True),
                      engine: AsyncEngine = Depends(get_db_engine)):
    """The company's top markets: its own first, then linked companies', then the tags'.
    Free-text tags ("ev", "microchips") are matched to map entities, by one Jev call if needed.
    With `rerank=true`, the tag query's top `candidates` events are scored by Jev for how much
    they matter to the company, and only those scoring 0.5 or more are returned, best first.
    With `traded` (the default) only markets that traded on their venue in the last week are
    returned (market_activity): an open market nobody trades has nothing to chart.
    """
    company = await db.get_entity(engine, company_id)
    if company is None or company["type"] != "company":
        raise HTTPException(404, f"Unknown company: {company_id}")
    try:
        matched = await asyncio.to_thread(match_tags, tags, _MAP) if tags else {}
    except JevError as exc:
        raise HTTPException(502, f"Tag matching failed: {exc}") from exc
    links = await db.graph_links(engine, company_id)

    tiers = {company_id: 1}
    tag_out = []
    for symbol in links:
        tiers.setdefault(symbol, 2)
    for tag, entity in matched.items():
        if entity is not None:
            tiers.setdefault(entity.symbol, 3)
            tag_out.append(TagOut(input=tag, id=entity.symbol, name=entity.name, category=entity.category,
                                  source="tag"))
    by_symbol = {e.symbol: e for e in _MAP.entities}
    tag_out += [TagOut(input=None, id=s, name=by_symbol[s].name if s in by_symbol else s, category="company",
                       source="graph") for s in links]
    markets = await db.top_markets(engine, tiers, candidates if rerank_ else limit * (TRADED_OVERSAMPLE if traded else 1))
    if traded:
        markets = await only_traded(markets)
    if not rerank_:
        markets = markets[:limit]
    if rerank_:
        related = [t.name for t in tag_out]
        try:
            scored = await asyncio.to_thread(rerank, company["name"], related, markets)
        except JevError as exc:
            raise HTTPException(502, f"Reranking failed: {exc}") from exc
        markets = [{**m, "relevance": round(score, 3)} for m, score in scored[:limit]]
    fields = TopMarketOut.model_fields
    return TopMarketsOut(company=_entity(company), tags=tag_out,
                         unmatched_tags=[t for t, e in matched.items() if e is None],
                         markets=[TopMarketOut(**{k: m[k] for k in fields if k in m}) for m in markets])
