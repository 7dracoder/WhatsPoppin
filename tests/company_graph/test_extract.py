import asyncio
import os
import re
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from backend.llm import client as llm_client
from backend.models.entity import Entity
from backend.models.entity_relationship import EntityRelationship
from company_graph import llm
from company_graph.companies import Company
from company_graph.db import create_tables
from company_graph.extract import (
    ExtractedRelationship,
    ExtractionResult,
    RelationshipRejected,
    check_relationship,
    extract,
    reverse_type,
    save_relationship,
)
from company_graph.trim import Chunk

TESLA = Company("TSLA", "Tesla, Inc.", 1318605)
PANASONIC = Company("PCRFY", "Panasonic Holdings Corp", 0)
URL = "https://www.sec.gov/Archives/edgar/data/1318605/000162828024002390/tsla-20231231.htm"
FETCHED = {URL}
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


# --- the 10-chunk accuracy set ---------------------------------------------------
# (filer, subject, passage, expected relationship types, the reply a correct model gives)
# The model is faked: each passage maps to a canned reply in the response model's shape. The set
# checks that prompt building, parsing and post-processing give the labelled answer for every
# chunk. test_live_accuracy runs the same set against the real model when asked to.

NVDA = Company("NVDA", "NVIDIA Corp", 1045810)
TSM = Company("TSM", "Taiwan Semiconductor Manufacturing Co Ltd", 1046179)
AAPL = Company("AAPL", "Apple Inc.", 320193)
MSFT = Company("MSFT", "Microsoft Corp", 789019)
WMT = Company("WMT", "Walmart Inc.", 104169)
PG = Company("PG", "Procter & Gamble Co", 80424)
AMD = Company("AMD", "Advanced Micro Devices Inc", 2488)
INTC = Company("INTC", "Intel Corp", 50863)
GOOGL = Company("GOOGL", "Alphabet Inc.", 1652044)
F = Company("F", "Ford Motor Co", 37996)
KO = Company("KO", "Coca-Cola Co", 21344)
GM = Company("GM", "General Motors Co", 1467858)
AMZN = Company("AMZN", "Amazon.com, Inc.", 1018724)

CASES = [
    # Five that state a relationship.
    (TESLA, PANASONIC,
     "We rely on Panasonic as the primary supplier of lithium-ion battery cells for our vehicles produced at "
     "Gigafactory Nevada, and any disruption in Panasonic's supply could harm our production.",
     ["supplier"], [("supplier", "Tesla states that Panasonic is the primary supplier of battery cells for its Gigafactory Nevada vehicles.")]),
    (NVDA, TSM,
     "We do not manufacture semiconductor wafers. We utilize Taiwan Semiconductor Manufacturing Company Limited "
     "to produce our wafers, and we depend on its capacity.",
     ["supplier"], [("supplier", "NVIDIA states that Taiwan Semiconductor Manufacturing produces its wafers.")]),
    (PG, WMT,
     "Our largest customer, Walmart Inc. and its affiliates, accounted for approximately 15% of consolidated net "
     "sales in fiscal 2024.",
     ["customer"], [("customer", "Procter & Gamble states that Walmart accounted for about 15% of its net sales in fiscal 2024.")]),
    (AMD, INTC,
     "Intel Corporation is our principal competitor in the microprocessor market, and we compete with Intel on "
     "performance, features and price.",
     ["competitor"], [("competitor", "AMD names Intel as its principal competitor in microprocessors.")]),
    (MSFT, Company("OpenAI", "OpenAI", 0),
     "We have a long-term partnership with OpenAI, including investments and an agreement under which Azure is "
     "the exclusive cloud provider for OpenAI's workloads.",
     ["partner"], [("partner", "Microsoft states that it has a long-term partnership with OpenAI, with Azure as OpenAI's exclusive cloud provider.")]),
    # Five passing mentions: the subject is named but no relationship with the filer is stated.
    (AAPL, GOOGL,
     "The smartphone market includes devices running operating systems developed by other companies, such as "
     "Android from Alphabet Inc., and the market is subject to rapid technological change.",
     [], []),
    (F, KO,
     "Our headquarters campus in Dearborn hosts community events, and local vendors such as Coca-Cola bottlers "
     "have participated in charity drives organized by employees.",
     [], []),
    (WMT, AMZN,
     "Our common stock is included in the S&P 500 index alongside other large retailers such as Amazon.com, Inc.",
     [], []),
    (GM, TESLA,
     "In 2023, total U.S. electric vehicle sales grew significantly, according to industry data published by "
     "third parties that track registrations of vehicles from Tesla and other manufacturers.",
     [], []),
    (INTC, MSFT,
     "Our board members include individuals who have previously served as executives at companies such as "
     "Microsoft Corporation and other technology firms.",
     [], []),
]


def _fake_model(cases):
    replies = {passage: reply for _, _, passage, _, reply in cases}

    def complete(system, user, response_model):
        assert response_model is ExtractionResult
        passage = re.search(r'Passage:\n"""\n(.*)\n"""', user, re.S).group(1)
        rels = [{"type": t, "summary": s} for t, s in replies[passage]]
        return response_model.model_validate({"relationships": rels})

    return complete


def _score(results):
    return sum(sorted(r.type for r in got) == sorted(expected) for got, expected in results)


def test_ten_chunk_accuracy_with_faked_model():
    fake = _fake_model(CASES)
    results = [(extract(Chunk(0, passage), filer, subject, complete_fn=fake), expected)
               for filer, subject, passage, expected, _ in CASES]
    assert len(results) == 10
    assert _score(results) >= 9


@pytest.mark.skipif(not (os.environ.get("GRAPH_LIVE_EVAL") == "1" and os.environ.get("OPENROUTER")),
                    reason="live model check; set GRAPH_LIVE_EVAL=1 and OPENROUTER to run it")
def test_live_accuracy():  # pragma: no cover - calls the real model
    results = [(extract(passage, filer, subject), expected) for filer, subject, passage, expected, _ in CASES]
    assert _score(results) >= 9


# --- extract ----------------------------------------------------------------------

def test_extract_sends_names_and_text_and_asks_for_facts_only():
    seen = {}

    def fake(system, user, response_model):
        seen.update(system=system, user=user)
        return ExtractionResult(relationships=[])

    assert extract("Panasonic supplies our cells.", TESLA, "Panasonic", complete_fn=fake) == []
    assert "Filer: Tesla, Inc." in seen["user"]
    assert "Subject: Panasonic" in seen["user"]
    assert "Panasonic supplies our cells." in seen["user"]
    system = seen["system"].lower()
    assert "empty list" in system
    assert "never predict stock prices" in system
    assert "never suggest buying, selling or holding" in system


def test_extract_drops_duplicate_types_and_empty_summaries():
    def fake(system, user, response_model):
        return ExtractionResult(relationships=[
            ExtractedRelationship(type="supplier", summary=" Panasonic supplies cells. "),
            ExtractedRelationship(type="supplier", summary="Again."),
            ExtractedRelationship(type="partner", summary="   "),
        ])

    out = extract("text", TESLA, PANASONIC, complete_fn=fake)
    assert [(r.type, r.summary) for r in out] == [("supplier", "Panasonic supplies cells.")]


def test_response_model_only_allows_known_types():
    with pytest.raises(ValueError):
        ExtractionResult.model_validate({"relationships": [{"type": "investor", "summary": "x"}]})


def test_extract_passes_llm_errors_through():
    def fake(system, user, response_model):
        raise llm_client.LlmError("down")

    with pytest.raises(llm_client.LlmError):
        extract("text", TESLA, PANASONIC, complete_fn=fake)


def test_reverse_type():
    assert reverse_type("supplier") == "customer"
    assert reverse_type("customer") == "supplier"
    assert reverse_type("competitor") == "competitor"


def test_llm_complete_uses_graph_llm_model(monkeypatch):
    calls = []

    def fake(system, user, response_model, model):
        calls.append(model)
        return response_model(relationships=[])

    monkeypatch.setattr(llm_client, "complete_structured", fake)
    monkeypatch.setenv("GRAPH_LLM_MODEL", "some/model")
    llm.complete("s", "u", ExtractionResult)
    monkeypatch.delenv("GRAPH_LLM_MODEL")
    llm.complete("s", "u", ExtractionResult)
    assert calls == ["some/model", llm_client.DEFAULT_MODEL]


def test_extract_goes_through_llm_complete_by_default(monkeypatch):
    def fake(system, user, response_model, model):
        return response_model(relationships=[{"type": "supplier", "summary": "Panasonic supplies cells."}])

    monkeypatch.setattr(llm_client, "complete_structured", fake)
    assert [r.type for r in extract("text", TESLA, PANASONIC)] == ["supplier"]


# --- save_relationship ------------------------------------------------------------

class _AsyncSession:
    """An AsyncSession stand-in over a sync SQLAlchemy Session on in-memory SQLite (aiosqlite is not installed)."""

    def __init__(self, sync_session):
        self.s = sync_session

    async def get(self, model, pk):
        return self.s.get(model, pk)

    def add(self, row):
        self.s.add(row)

    async def scalar(self, stmt):
        return self.s.scalar(stmt)


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    event.listen(eng, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    with eng.begin() as conn:
        create_tables(conn)
    with Session(eng) as s:
        yield s
    eng.dispose()


def _save(session, **kw):
    args = dict(entity=TESLA, related=PANASONIC, relationship_type="supplier",
                summary="Panasonic supplies battery cells.", evidence_url=URL, fetched_urls=FETCHED, now=NOW)
    args.update(kw)
    return asyncio.run(save_relationship(_AsyncSession(session), **args))


def test_save_inserts_entities_and_relationship(session):
    row = _save(session)
    session.commit()
    assert {e.symbol for e in session.scalars(select(Entity))} == {"TSLA", "PCRFY"}
    saved = session.scalars(select(EntityRelationship)).one()
    assert saved is row
    assert (saved.entity_symbol, saved.related_entity_symbol, saved.relationship_type) == ("TSLA", "PCRFY", "supplier")
    assert float(saved.confidence) == 0.9 and saved.source == "filing" and float(saved.weight) == 1
    assert saved.summary == "Panasonic supplies battery cells." and saved.evidence_url == URL


def test_save_twice_updates_the_same_row(session):
    other = URL.replace("2023", "2024")
    _save(session)
    session.commit()
    _save(session, summary="Panasonic is the main cell supplier.", evidence_url=other, fetched_urls={other})
    session.commit()
    rows = session.scalars(select(EntityRelationship)).all()
    assert len(rows) == 1
    assert rows[0].summary == "Panasonic is the main cell supplier." and rows[0].evidence_url == other


def test_sector_save_does_not_overwrite_filing_row(session):
    _save(session, relationship_type="competitor")
    session.commit()
    _save(session, relationship_type="competitor", source="sector", summary="Same industry.")
    session.commit()
    row = session.scalars(select(EntityRelationship)).one()
    assert row.source == "filing" and row.summary == "Panasonic supplies battery cells."


def test_save_non_us_company_uses_name_as_symbol(session):
    catl = Company("CATL", "CATL", 0)
    _save(session, related=catl)
    session.commit()
    assert session.get(Entity, "CATL").type == "company"


# --- rejection rules: one test each ----------------------------------------------

def test_rejects_unknown_type(session):
    with pytest.raises(RelationshipRejected, match="type"):
        _save(session, relationship_type="investor")
    assert session.scalars(select(Entity)).all() == []


def test_rejects_missing_evidence_url(session):
    for missing in (None, "", "   "):
        with pytest.raises(RelationshipRejected, match="missing"):
            _save(session, evidence_url=missing)
    assert session.scalars(select(Entity)).all() == []


def test_rejects_evidence_url_not_fetched_in_this_run(session):
    with pytest.raises(RelationshipRejected, match="not fetched"):
        _save(session, evidence_url="https://www.sec.gov/Archives/made-up.htm")
    with pytest.raises(RelationshipRejected, match="not fetched"):
        _save(session, fetched_urls=set())
    assert session.scalars(select(Entity)).all() == []


def test_rejects_self_link(session):
    with pytest.raises(RelationshipRejected, match="itself"):
        _save(session, related=TESLA)
    with pytest.raises(RelationshipRejected, match="itself"):
        _save(session, related=Company("Tesla", "Tesla Inc", 0))  # same company under its name
    assert session.scalars(select(Entity)).all() == []


def test_rejects_unknown_source():
    with pytest.raises(RelationshipRejected, match="source"):
        check_relationship(TESLA, PANASONIC, "supplier", URL, FETCHED, source="model")


def test_check_accepts_valid_edge():
    check_relationship(TESLA, PANASONIC, "supplier", URL, FETCHED)
