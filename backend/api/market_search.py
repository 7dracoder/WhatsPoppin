"""`GET /markets/search`: a plain-English risk or event against every open prediction market.

"chip export controls china" -> the markets about it, ranked by embedding similarity
(voyage/voyage-finance-2 through Nicrron; backend/enrichment/vectors.py). One market per event: the
soonest-closing one. The first search after the API starts embeds any active event that has no
vector yet (a few seconds per few hundred events); later searches only embed the query.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.api.deps import get_db_engine
from backend.api.market_activity import only_traded
from backend.enrichment.vectors import search_events
from backend.llm.embeddings import EmbeddingError, embed_model
from backend.observability import trace_run

log = logging.getLogger(__name__)

router = APIRouter(prefix="/markets", tags=["market-search"])

DEFAULT_MIN_SCORE = 0.3  # below this, voyage-finance-2 matches are unrelated in practice
TRADED_OVERSAMPLE = 3    # read this many times `limit` so dropping untraded markets still fills the list


class MarketMatchOut(BaseModel):
    source: str
    market_id: str
    event_id: str | None
    title: str | None
    outcome_label: str | None
    event_title: str | None
    rules_primary: str | None
    close_time: datetime | None
    url: str | None
    score: float  # cosine similarity to the query, 0-1 in practice
    model: str    # the embedding model that ranked it


def get_search_fn():
    # A dependency so tests can swap in a fake.
    return search_events


@router.get("/search", response_model=list[MarketMatchOut])
async def search_markets(q: str = Query(min_length=2, max_length=300), limit: int = Query(10, ge=1, le=50),
                         min_score: float = Query(DEFAULT_MIN_SCORE, ge=0, le=1), traded: bool = Query(True),
                         engine: AsyncEngine = Depends(get_db_engine), search_fn=Depends(get_search_fn)):
    """Open markets about `q`, most similar first. With `traded` (the default) only markets that
    traded on their venue in the last week, so every result has a chart."""
    model = embed_model()
    with trace_run("markets.search", inputs={"q": q, "limit": limit, "min_score": min_score}, model=model) as run:
        try:
            with run.step("embeddings.search", kind="retrieval", model=model) as step:
                hits = await search_fn(engine, q.strip(), limit * (TRADED_OVERSAMPLE if traded else 1), min_score)
                step.output = [{"event": (m[0].get("event_title") or m[0].get("title")), "score": round(s, 4)}
                               for m, s in hits[:10]]
        except EmbeddingError as exc:
            raise HTTPException(502, f"Market search failed: {exc}") from exc
        rows = [{**markets[0], "score": round(score, 4)} for markets, score in hits]
        if traded:
            with run.step("venue.activity_filter", kind="tool") as step:
                rows = await only_traded(rows)
                step.output = {"kept": len(rows)}
        fields = MarketMatchOut.model_fields
        out = [MarketMatchOut(**{k: r.get(k) for k in fields if k != "model"}, model=model) for r in rows[:limit]]
        run.output = {"results": len(out)}
        return out
