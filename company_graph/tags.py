"""F14. Relationship tags: short keywords from an entity_relationship's `summary`, for
matching against Kalshi market categories (a separate, later feature — not built here).

Not an API endpoint and not wired into F4/F5's save_relationship. A one-shot script: tags
every `entity_relationships` row with `tags IS NULL` and exits. Run it on demand, after a
link run or whenever there's new untagged data:

    uv run python -m company_graph.tags

llm.complete is synchronous, so it runs through asyncio.to_thread, matching F6/F8.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel
from sqlalchemy import func, or_, select

from backend.models.entity import Entity
from backend.models.entity_relationship import EntityRelationship
from company_graph import llm

__all__ = ["RelationshipTags", "generate_tags", "tag_relationship", "tag_missing", "tag_all",
          "get_company_tags", "MAX_TAGS"]

log = logging.getLogger("company_graph.tags")

MAX_TAGS = 5

TAG_SYSTEM_PROMPT = (
    "You read one sentence describing a business relationship between two companies and list "
    f"up to {MAX_TAGS} short, lowercase keywords for it: commodities, sectors, geographic "
    "chokepoints, or company names that a prediction-market title might use (for example "
    "'oil', 'memory', 'strait of hormuz', 'shell'). The tags do not have to name either company "
    "in the sentence — the goal is to surface indirect dependencies, such as a company that "
    "depends on memory chips depending, two steps removed, on whatever those chips are made "
    "from or shipped through. Return an empty list if nothing applies."
)


class RelationshipTags(BaseModel):
    tags: list[str]


def generate_tags(summary: str | None) -> list[str]:
    """One model call for one relationship's `summary`. [] without calling the model if there's
    no summary to read. Raises LlmError on failure; callers decide whether to skip or retry.
    """
    if not summary or not summary.strip():
        return []
    result = llm.complete(TAG_SYSTEM_PROMPT, summary, RelationshipTags)
    tags = [t.strip().lower() for t in result.tags if t.strip()]
    return tags[:MAX_TAGS]


async def tag_relationship(session, relationship: EntityRelationship) -> None:
    """Generate and save tags for one relationship. Flushes; the caller's session commits."""
    relationship.tags = await asyncio.to_thread(generate_tags, relationship.summary)
    await session.flush()


async def tag_missing(session, limit: int = 50) -> int:
    """Tag up to `limit` relationships with `tags IS NULL`. Returns how many were tagged."""
    rows = (await session.execute(
        select(EntityRelationship).where(EntityRelationship.tags.is_(None)).limit(limit)
    )).scalars().all()
    for row in rows:
        await tag_relationship(session, row)
    return len(rows)


async def get_company_tags(session, name_or_symbol: str) -> list[str]:
    """Every tag across every relationship naming this company, on either side of the edge
    (its own filings' edges and the edges of companies that name it). Accepts a ticker
    ("NVDA") or a company name ("NVIDIA"), matched case-insensitively against the local
    `entities` table (no SEC lookup, no network call). Deduplicated, in first-seen order.
    [] for an unknown company or one with no tagged edges.
    """
    symbol = await _resolve_symbol(session, name_or_symbol)
    if symbol is None:
        return []
    rows = (await session.execute(
        select(EntityRelationship.tags).where(
            or_(EntityRelationship.entity_symbol == symbol, EntityRelationship.related_entity_symbol == symbol)
        )
    )).scalars().all()
    seen: list[str] = []
    for row_tags in rows:
        for tag in row_tags or []:
            if tag not in seen:
                seen.append(tag)
    return seen


async def _resolve_symbol(session, name_or_symbol: str) -> str | None:
    text = name_or_symbol.strip()
    if not text:
        return None
    exact = (await session.execute(
        select(Entity.symbol).where(
            or_(func.lower(Entity.symbol) == text.lower(), func.lower(Entity.name) == text.lower())
        )
    )).scalars().first()
    if exact is not None:
        return exact
    # SEC names carry suffixes ("NVIDIA CORP"), so a bare "NVIDIA" needs a substring match.
    partial = (await session.execute(
        select(Entity.symbol).where(func.lower(Entity.name).contains(text.lower()))
    )).scalars().first()
    if partial is not None:
        return partial
    # Not in `entities` at all; fall back to treating the input as already a symbol, same as
    # entity_relationships stores it (covers a company the DB has never resolved by name, only
    # ever seen as a bare ticker on the related side of an edge).
    return text.upper()


async def tag_all(database_url: str, batch_size: int = 50) -> int:
    """Connect once, tag every untagged relationship (in batches), then exit. Returns the
    total tagged. A failure (a dropped connection, or an LlmError from a bad model response)
    stops the run; whatever was tagged in earlier batches is already committed.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    from company_graph.db import create_tables, make_engine

    if not database_url:
        raise SystemExit("Set DATABASE_URL in .env")
    engine = make_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(create_tables)
        total = 0
        while True:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                tagged = await tag_missing(session, limit=batch_size)
                await session.commit()
            total += tagged
            if tagged:
                log.info("Tagged %d relationship(s) (%d so far)", tagged, total)
            if tagged < batch_size:
                break
        return total
    finally:
        await engine.dispose()


if __name__ == "__main__":
    from dotenv import load_dotenv

    from company_graph.config import load

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load()
    total = asyncio.run(tag_all(cfg.database_url))
    log.info("Done. Tagged %d relationship(s) total.", total)
