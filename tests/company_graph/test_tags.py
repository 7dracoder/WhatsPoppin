import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.models.entity import Entity
from backend.models.entity_relationship import EntityRelationship
from company_graph import llm, tags
from company_graph.db import create_tables


class FakeAsyncSession:
    """AsyncSession-shaped wrapper over an in-memory SQLite session (no real database)."""

    def __init__(self, sync_session):
        self.sync = sync_session

    def add(self, obj):
        self.sync.add(obj)

    async def execute(self, stmt):
        return self.sync.execute(stmt)

    async def flush(self):
        self.sync.flush()


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        create_tables(conn)
    with Session(engine) as s:
        yield FakeAsyncSession(s)


def relationship(summary="NVIDIA states it buys memory from SK hynix.", tags_=None, related="SKHY"):
    return EntityRelationship(
        entity_symbol="NVDA", related_entity_symbol=related, relationship_type="supplier",
        confidence=Decimal("0.9"), source="filing", summary=summary, tags=tags_,
    )


class FakeComplete:
    """Stands in for llm.complete: records every call and returns canned tags."""

    def __init__(self, tags_=("memory", "semiconductors"), error=None):
        self.calls = []
        self.tags = list(tags_)
        self.error = error

    def __call__(self, system, user, response_model, model=None):
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return response_model(tags=self.tags)


@pytest.fixture
def complete(monkeypatch):
    fake = FakeComplete()
    monkeypatch.setattr(llm, "complete", fake)
    return fake


# --- generate_tags ---------------------------------------------------------------------------

def test_no_summary_returns_empty_and_calls_nothing():
    # conftest's no_real_model_calls makes any real call raise, so this also proves no call happened.
    assert tags.generate_tags(None) == []
    assert tags.generate_tags("   ") == []


def test_generate_tags_passes_summary_as_the_user_message(complete):
    result = tags.generate_tags("NVIDIA states it buys memory from SK hynix.")
    assert result == ["memory", "semiconductors"]
    system, user = complete.calls[0]
    assert user == "NVIDIA states it buys memory from SK hynix."
    assert "lowercase keywords" in system


def test_generate_tags_strips_blanks_lowercases_and_caps_at_max(monkeypatch):
    fake = FakeComplete(tags_=["Oil", "", "  Shipping  ", "A", "B", "C", "D"])
    monkeypatch.setattr(llm, "complete", fake)
    result = tags.generate_tags("some summary")
    assert len(result) == tags.MAX_TAGS
    assert result[0] == "oil"
    assert "shipping" in result[1] or "shipping" in result  # trimmed, not padded with spaces


def test_generate_tags_propagates_llm_error(monkeypatch):
    monkeypatch.setattr(llm, "complete", FakeComplete(error=llm.LlmError("overloaded")))
    with pytest.raises(llm.LlmError):
        tags.generate_tags("some summary")


# --- tag_relationship --------------------------------------------------------------------------

def test_tag_relationship_sets_tags_and_flushes(session, complete):
    row = relationship()
    session.add(row)
    asyncio.run(session.flush())
    asyncio.run(tags.tag_relationship(session, row))
    assert row.tags == ["memory", "semiconductors"]


# --- tag_missing --------------------------------------------------------------------------------

def test_tag_missing_only_tags_null_rows(session, complete):
    already = relationship(related="TSM", tags_=["existing"])
    untagged = relationship(related="SKHY")
    session.add(already)
    session.add(untagged)
    asyncio.run(session.flush())

    tagged_count = asyncio.run(tags.tag_missing(session))

    assert tagged_count == 1
    assert len(complete.calls) == 1
    rows = {r.related_entity_symbol: r.tags for r in session.sync.execute(select(EntityRelationship)).scalars()}
    assert rows["TSM"] == ["existing"]
    assert rows["SKHY"] == ["memory", "semiconductors"]


def test_tag_missing_rerun_tags_nothing_the_second_time(session, complete):
    session.add(relationship(related="SKHY"))
    asyncio.run(session.flush())

    first = asyncio.run(tags.tag_missing(session))
    second = asyncio.run(tags.tag_missing(session))

    assert first == 1
    assert second == 0
    assert len(complete.calls) == 1  # the second pass made no model call


def test_tag_missing_respects_limit(session, complete):
    for i in range(3):
        session.add(relationship(related=f"X{i}"))
    asyncio.run(session.flush())

    tagged_count = asyncio.run(tags.tag_missing(session, limit=2))

    assert tagged_count == 2
    assert len(complete.calls) == 2


def test_relationship_with_no_summary_in_the_batch_makes_no_model_call(session, complete):
    session.add(relationship(summary=None, related="SKHY"))
    asyncio.run(session.flush())

    tagged_count = asyncio.run(tags.tag_missing(session))

    assert tagged_count == 1
    assert complete.calls == []
    row = session.sync.execute(select(EntityRelationship)).scalars().first()
    assert row.tags == []


# --- get_company_tags --------------------------------------------------------------------------

def entity(symbol, name):
    return Entity(symbol=symbol, name=name, type="company")


def test_get_company_tags_by_symbol_collects_both_sides_of_the_edge(session):
    session.add(relationship(related="TSM", tags_=["semiconductors", "taiwan"]))
    session.add(EntityRelationship(  # NVDA is the *related* side here, not entity_symbol
        entity_symbol="MSFT", related_entity_symbol="NVDA", relationship_type="partner",
        confidence=Decimal("0.9"), source="filing", summary="x", tags=["xbox", "gaming"],
    ))
    session.add(relationship(related="MSFT", tags_=["cloud"]))  # unrelated-looking but still names NVDA
    asyncio.run(session.flush())

    result = asyncio.run(tags.get_company_tags(session, "NVDA"))

    assert result == ["semiconductors", "taiwan", "xbox", "gaming", "cloud"]


def test_get_company_tags_resolves_a_company_name(session):
    session.add(entity("NVDA", "NVIDIA CORP"))
    session.add(relationship(related="TSM", tags_=["semiconductors"]))
    asyncio.run(session.flush())

    assert asyncio.run(tags.get_company_tags(session, "NVIDIA")) == ["semiconductors"]
    assert asyncio.run(tags.get_company_tags(session, "nvidia corp")) == ["semiconductors"]  # case-insensitive


def test_get_company_tags_excludes_other_companies_edges(session):
    session.add(relationship(related="TSM", tags_=["semiconductors"]))
    session.add(EntityRelationship(  # names neither TSM nor NVDA; must not show up for TSM's lookup
        entity_symbol="MSFT", related_entity_symbol="GOOGL", relationship_type="competitor",
        confidence=Decimal("0.9"), source="filing", summary="x", tags=["cloud", "azure"],
    ))
    asyncio.run(session.flush())

    assert asyncio.run(tags.get_company_tags(session, "TSM")) == ["semiconductors"]


def test_get_company_tags_dedupes_across_edges(session):
    session.add(relationship(related="TSM", tags_=["semiconductors", "taiwan"]))
    session.add(relationship(related="SKHY", tags_=["semiconductors", "memory"]))
    asyncio.run(session.flush())

    assert asyncio.run(tags.get_company_tags(session, "NVDA")) == ["semiconductors", "taiwan", "memory"]


def test_get_company_tags_returns_empty_for_unknown_company(session):
    assert asyncio.run(tags.get_company_tags(session, "NOT_A_REAL_COMPANY")) == []


def test_get_company_tags_falls_back_to_the_input_as_a_symbol_when_not_in_entities(session):
    # "SKHY" has no `entities` row in this test, so the name/symbol lookup misses; the
    # function should still treat it as a symbol rather than giving up, since that's how
    # entity_relationships stores it regardless of whether `entities` ever got a row for it.
    session.add(relationship(related="SKHY", tags_=["memory", "hbm"]))
    asyncio.run(session.flush())

    assert asyncio.run(tags.get_company_tags(session, "skhy")) == ["memory", "hbm"]
