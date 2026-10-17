from datetime import datetime, timezone
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from backend.api import entities
from backend.main import app
from backend.entities import CATEGORIES

TESLA = {"symbol": "TSLA", "name": "Tesla, Inc.", "type": "company"}


@pytest.fixture
def client(monkeypatch):
    calls = {}

    async def autocomplete(engine, q, symbols, category, limit):
        calls["autocomplete"] = (q, category, limit, "TSLA" in symbols)
        return [TESLA] if "tesla".startswith(q.lower()) else []

    async def get_entity(engine, symbol):
        return TESLA if symbol == "TSLA" else None

    async def markets_for_entity(engine, symbol, limit):
        return [
            {"source": "kalshi", "market_id": "KXTSLA-1", "title": "Tesla deliveries?", "outcome_label": None,
             "event_title": None, "status": "active", "close_time": datetime(2026, 12, 31, tzinfo=timezone.utc),
             "url": "https://kalshi.com/markets/kxtsla"},
            {"source": "polymarket_us", "market_id": "tsla-q4", "title": "Tesla above $400?", "outcome_label": "Yes",
             "event_title": "Tesla price", "status": "active", "close_time": None, "url": None},
        ]

    for name, fn in [("autocomplete", autocomplete), ("get_entity", get_entity),
                     ("markets_for_entity", markets_for_entity)]:
        monkeypatch.setattr(entities.db, name, fn)
    app.dependency_overrides[entities.get_db_engine] = lambda: None  # no database
    test_client = TestClient(app)
    test_client.calls = calls
    yield test_client
    app.dependency_overrides.clear()


def test_autocomplete_tes_returns_tesla(client):
    response = client.get("/entities/autocomplete", params={"q": "Tes"})
    assert response.status_code == 200
    assert response.json() == [{"id": "TSLA", "name": "Tesla, Inc.", "category": "company"}]
    assert client.calls["autocomplete"] == ("Tes", None, 10, True)  # restricted to the map


def test_autocomplete_validates_input(client):
    assert client.get("/entities/autocomplete", params={"q": ""}).status_code == 422
    assert client.get("/entities/autocomplete", params={"q": "x", "category": "planet"}).status_code == 422


def test_entity_markets_across_sources(client):
    body = client.get("/entities/TSLA/markets").json()
    assert body["entity"]["id"] == "TSLA"
    assert {m["source"] for m in body["markets"]} == {"kalshi", "polymarket_us"}


def test_unknown_entity_is_404(client):
    assert client.get("/entities/NOPE/markets").status_code == 404


def test_category_values_match_the_map():
    assert set(get_args(entities.Category)) == set(CATEGORIES)


@pytest.fixture
def top_client(monkeypatch):
    from backend.entities import MapEntity

    calls = {}

    async def get_entity(engine, symbol):
        return {"NVDA": {"symbol": "NVDA", "name": "NVIDIA Corporation", "type": "company"},
                "Gold": {"symbol": "Gold", "name": "Gold", "type": "resource"}}.get(symbol)

    async def graph_links(engine, symbol):
        return ["TSM"]

    async def top_markets(engine, tiers, limit):
        calls["tiers"], calls["limit"] = tiers, limit
        return [{"source": "kalshi", "market_id": "KXA100-1", "event_id": "KXA100", "title": "A100 price?",
                 "outcome_label": "Above $0.50", "event_title": "NVIDIA A100", "close_time": None, "url": None,
                 "tier": 1, "matched": ["NVDA", "Semiconductors"], "event_key": "KXA100"},
                {"source": "kalshi", "market_id": "KXH200-DEC", "event_id": "KXH200", "title": "H200 price?",
                 "outcome_label": "Above $1.50", "event_title": "NVIDIA H200", "close_time": None, "url": None,
                 "tier": 1, "matched": ["NVDA"], "event_key": "KXH200"}]

    async def only_traded(markets, now=None):
        calls["checked"] = [m["market_id"] for m in markets]
        return [m for m in markets if m["market_id"] != "KXH200-DEC"]  # H200 never traded

    def match_tags(tags, entity_map):
        semis = MapEntity(symbol="Semiconductors", name="Semiconductors", category="sector")
        return {t: (semis if t == "microchips" else None) for t in tags}

    for name, fn in [("get_entity", get_entity), ("graph_links", graph_links), ("top_markets", top_markets)]:
        monkeypatch.setattr(entities.db, name, fn)
    monkeypatch.setattr(entities, "match_tags", match_tags)
    monkeypatch.setattr(entities, "only_traded", only_traded)
    app.dependency_overrides[entities.get_db_engine] = lambda: None
    test_client = TestClient(app)
    test_client.calls = calls
    yield test_client
    app.dependency_overrides.clear()


def test_top_markets_ranks_company_then_links_then_tags(top_client):
    body = top_client.get("/entities/NVDA/top-markets", params=[("tags", "microchips"), ("tags", "banana")]).json()
    assert top_client.calls["tiers"] == {"NVDA": 1, "TSM": 2, "Semiconductors": 3}
    assert top_client.calls["limit"] == 3 * entities.TRADED_OVERSAMPLE  # extra rows, since untraded ones are dropped
    assert [m["market_id"] for m in body["markets"]] == ["KXA100-1"]  # the untraded H200 market is left out
    assert [(t["input"], t["id"], t["source"]) for t in body["tags"]] == [
        ("microchips", "Semiconductors", "tag"), (None, "TSM", "graph")]
    assert body["unmatched_tags"] == ["banana"]
    assert body["markets"][0]["tier"] == 1 and body["markets"][0]["matched"] == ["NVDA", "Semiconductors"]


def test_untraded_markets_stay_when_asked(top_client):
    body = top_client.get("/entities/NVDA/top-markets", params={"traded": "false", "limit": 1}).json()
    assert top_client.calls["limit"] == 1 and "checked" not in top_client.calls
    assert [m["market_id"] for m in body["markets"]] == ["KXA100-1"]


def test_top_markets_needs_a_company(top_client):
    assert top_client.get("/entities/Gold/top-markets").status_code == 404
    assert top_client.get("/entities/NOPE/top-markets").status_code == 404


def test_rerank_scores_the_candidates_and_returns_the_best(top_client, monkeypatch):
    def rerank(company, related, markets):
        top_client.calls["rerank"] = (company, related, len(markets))
        return [(markets[0], 0.912)]

    monkeypatch.setattr(entities, "rerank", rerank)
    body = top_client.get("/entities/NVDA/top-markets",
                          params=[("tags", "microchips"), ("rerank", "true"), ("candidates", "40")]).json()
    assert top_client.calls["limit"] == 40  # candidates, not limit, are fetched
    # TSM is in the map now, so the rerank gets its name rather than the bare ticker.
    assert top_client.calls["rerank"] == ("NVIDIA Corporation", ["Semiconductors", "Taiwan Semiconductor Manufacturing Company"], 1)
    assert body["markets"][0]["relevance"] == 0.912


def test_without_rerank_relevance_is_empty(top_client):
    body = top_client.get("/entities/NVDA/top-markets").json()
    assert body["markets"][0]["relevance"] is None
