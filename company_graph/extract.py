"""Relationship extractor (F4): ask the model what a filing passage states, then save it.

Direction convention, used everywhere in this module: a relationship found in a filing is
stored with the filer as `entity_symbol` and the other company (the subject) as
`related_entity_symbol`, and `relationship_type` is the subject's role for the filer, as in
`entity_relationships`: "supplier" means the subject supplies the filer. A caller that wants
the other company as the entity flips the type with `reverse_type`.

"Fetched in this run": every caller of `save_relationship` passes `fetched_urls`, the set of
URLs it actually downloaded during the current link run (for example every filing URL
handed to SecClient.fetch_text). An evidence URL outside that set is rejected, so no link is
ever saved from a URL the model or a stale cache made up.

`extract` is synchronous (it calls the model); call it from async code through
asyncio.to_thread. `save_relationship` is async and takes a SQLAlchemy AsyncSession.
Neither commits.
"""

from collections.abc import Callable, Collection
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select

from company_graph import llm
from company_graph.companies import Company, ensure_entity, normalize_name
from company_graph.schemas import CONFIDENCE, DEFAULT_WEIGHT, RELATIONSHIP_TYPES, RelationshipType

MAX_CHUNK_CHARS = 12_000  # a merged F3 chunk is a few thousand characters; this only guards against runaway input

_REVERSE = {"supplier": "customer", "customer": "supplier"}  # partner, competitor, sector_peer are symmetric


# --- model call -----------------------------------------------------------------

class ExtractedRelationship(BaseModel):
    type: RelationshipType = Field(description="The subject company's role for the filer.")
    summary: str = Field(description="One factual sentence saying what the text states about the two companies.")


class ExtractionResult(BaseModel):
    relationships: list[ExtractedRelationship] = Field(default_factory=list)


SYSTEM_PROMPT = """You read passages from SEC filings and report business relationships that the passage states.

You are given the filer (the company that wrote the filing), a subject company, and a passage.
Report a relationship only when the passage itself states a business relationship between the
filer and the subject. Give the subject's role for the filer:
- supplier: the subject supplies goods, parts or services to the filer
- customer: the subject buys from the filer, or accounts for part of the filer's revenue
- partner: the two have a collaboration, joint venture, licensing or other partnership agreement
- competitor: the filer names the subject as a company it competes with
- sector_peer: the filer names the subject only as a company in the same industry, with no other relationship

Rules:
- Use only the passage. Do not use anything you know about the companies from elsewhere.
- A passing mention is not a relationship: the subject appearing in a list, an example, a market
  description, a lawsuit, an index or a sentence that does not connect it to the filer's business
  gets no relationship.
- Report only current commercial relationships in the companies' main lines of business. These
  are NOT relationships: landlord or tenant, property leases, lenders, banks, underwriters,
  insurers, auditors, law firms, shareholders or fund holdings, lawsuit opponents, charities,
  relationships that ended, and anything possible, planned or hypothetical ("may", "could",
  "if it becomes", "in discussions").
- Use competitor only when the filer itself names the subject as a competitor today.
- If the passage states no relationship, return an empty list: {"relationships": []}.
- Each summary is one short factual sentence about what the passage states, naming both companies.
- Write facts only. Never predict stock prices or business results, never give an opinion on the
  companies, and never suggest buying, selling or holding any security."""


def _chunk_text(chunk) -> str:
    return getattr(chunk, "text", chunk)


def _name(company: Company | str) -> str:
    return company.name if isinstance(company, Company) else str(company)


def build_user_prompt(chunk, filer: Company | str, subject: Company | str) -> str:
    text = _chunk_text(chunk)[:MAX_CHUNK_CHARS]
    return f"Filer: {_name(filer)}\nSubject: {_name(subject)}\n\nPassage:\n\"\"\"\n{text}\n\"\"\""


def extract(
    chunk,
    filer: Company | str,
    subject: Company | str,
    complete_fn: Callable | None = None,
) -> list[ExtractedRelationship]:
    """The relationships between `filer` and `subject` that `chunk` states (often none).

    `chunk` is an F3 trim.Chunk or plain text. Each result's `type` is the subject's role for the
    filer. Duplicate types are dropped (first kept) and empty summaries are skipped. Raises
    LlmError when the model call fails. `complete_fn` defaults to company_graph.llm.complete and is
    there for tests.
    """
    complete_fn = complete_fn or llm.complete
    result = complete_fn(SYSTEM_PROMPT, build_user_prompt(chunk, filer, subject), ExtractionResult)
    out: list[ExtractedRelationship] = []
    seen: set[str] = set()
    for rel in result.relationships:
        summary = rel.summary.strip()
        if rel.type in seen or not summary:
            continue
        seen.add(rel.type)
        out.append(ExtractedRelationship(type=rel.type, summary=summary))
    return out


def reverse_type(relationship_type: str) -> str:
    """The same relationship seen from the other company: supplier <-> customer, the rest unchanged."""
    return _REVERSE.get(relationship_type, relationship_type)


# --- saving ---------------------------------------------------------------------

class RelationshipRejected(ValueError):
    """save_relationship refused the edge; the message says which rule failed."""


def check_relationship(
    entity: Company,
    related: Company,
    relationship_type: str,
    evidence_url: str | None,
    fetched_urls: Collection[str],
    source: str = "filing",
) -> None:
    """Raise RelationshipRejected unless the edge may be saved. Pure, no I/O.

    Rules: the type is one of RELATIONSHIP_TYPES; the source is one of CONFIDENCE's keys; the
    evidence URL is present and is in `fetched_urls` (the URLs fetched in this run); the related
    company is not the entity itself (same symbol, or same normalized name).
    """
    if relationship_type not in RELATIONSHIP_TYPES:
        raise RelationshipRejected(f"relationship type {relationship_type!r} is not one of {RELATIONSHIP_TYPES}")
    if source not in CONFIDENCE:
        raise RelationshipRejected(f"source {source!r} is not one of {tuple(CONFIDENCE)}")
    if not evidence_url or not evidence_url.strip():
        raise RelationshipRejected("evidence URL is missing")
    if evidence_url not in fetched_urls:
        raise RelationshipRejected(f"evidence URL was not fetched in this run: {evidence_url}")
    same_symbol = entity.symbol.strip().upper() == related.symbol.strip().upper()
    same_name = bool(normalize_name(entity.name)) and normalize_name(entity.name) == normalize_name(related.name)
    if same_symbol or same_name:
        raise RelationshipRejected(f"{related.symbol} is the entity itself")


async def save_relationship(
    session,
    entity: Company,
    related: Company,
    relationship_type: str,
    summary: str,
    evidence_url: str | None,
    fetched_urls: Collection[str],
    source: str = "filing",
    now: datetime | None = None,
    relationship_model=None,
):
    """Validate, ensure both `entities` rows, and upsert one `entity_relationships` row. Does not commit.

    `session` is a SQLAlchemy AsyncSession. `fetched_urls` is the set of URLs fetched in the
    current run (see the module docstring); `check_relationship` lists the rejection rules, and a
    rejected edge raises RelationshipRejected before anything is written. A related company with no
    US ticker is passed as Company(symbol=name, name=name, cik=0).

    An existing row for the same (entity, related, type) is updated: summary, evidence URL,
    confidence, source and last_confirmed_at. A "sector" save never overwrites a "filing" row; it
    only returns it. Returns the row.
    """
    check_relationship(entity, related, relationship_type, evidence_url, fetched_urls, source)
    if relationship_model is None:
        from backend.models.entity_relationship import EntityRelationship as relationship_model  # noqa: N813

    now = now or datetime.now(timezone.utc)
    await ensure_entity(session, entity)
    await ensure_entity(session, related)

    m = relationship_model
    row = await session.scalar(
        select(m).where(
            m.entity_symbol == entity.symbol,
            m.related_entity_symbol == related.symbol,
            m.relationship_type == relationship_type,
        )
    )
    if row is not None and row.source == "filing" and source == "sector":
        return row
    if row is None:
        row = m(entity_symbol=entity.symbol, related_entity_symbol=related.symbol,
                relationship_type=relationship_type, weight=DEFAULT_WEIGHT)
        session.add(row)
    row.confidence = CONFIDENCE[source]
    row.source = source
    row.summary = summary.strip()
    row.evidence_url = evidence_url
    row.last_confirmed_at = now
    return row
