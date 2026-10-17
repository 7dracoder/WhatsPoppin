from fastapi.testclient import TestClient

from backend.api import market_search
from backend.api.deps import get_db_engine
from backend.llm.embeddings import EmbeddingError
from backend.main import app

MARKET = {"source": "polymarket", "market_id": "m1", "event_id": "e1", "series_id": None, "title": "Cut?",
          "outcome_label": "Yes", "event_title": "Fed decision in December?", "rules_primary": None,
          "close_time": None, "url": "https://polymarket.com/event/x"}


def client_with(search_fn):
    async def fake_search(engine, q, limit, min_score):
        return await search_fn(q, limit, min_score)

    app.dependency_overrides[get_db_engine] = lambda: None
    app.dependency_overrides[market_search.get_search_fn] = lambda: fake_search
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def test_search_returns_ranked_markets_with_scores():
    seen = {}

    async def search(q, limit, min_score):
        seen.update(q=q, limit=limit, min_score=min_score)
        return [([MARKET], 0.381234)]

    body = client_with(search).get("/markets/search", params={"q": " rate cut ", "limit": 4, "traded": False}).json()
    assert seen == {"q": "rate cut", "limit": 4, "min_score": 0.3}
    assert body[0]["market_id"] == "m1" and body[0]["score"] == 0.3812
    assert body[0]["model"] == "voyage/voyage-finance-2"


def test_embedding_failure_is_a_502():
    async def search(q, limit, min_score):
        raise EmbeddingError("down")

    assert client_with(search).get("/markets/search", params={"q": "oil", "traded": False}).status_code == 502


def test_query_is_required():
    async def search(q, limit, min_score):
        return []

    assert client_with(search).get("/markets/search").status_code == 422
