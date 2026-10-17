from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.api import alerts
from backend.main import app

ALERT = {"id": 7, "created_at": datetime(2026, 10, 10, 22, tzinfo=timezone.utc), "source": "kalshi",
         "market_id": "KXB200-1", "title": "B200 above $6.50?", "event_title": "NVIDIA B200 price",
         "url": "https://kalshi.com/markets/kxb200", "direction": "yes_up", "summary": "Price jumped 8 pts",
         "score": 4.2}


@pytest.fixture
def client(monkeypatch):
    calls = {}

    async def recent_alerts(engine, since, limit, per_alert):
        calls["recent"] = (limit, per_alert)
        return [{**ALERT, "companies": [{"symbol": "NVDA", "name": "NVIDIA CORP", "tier": 1, "matched": ["NVDA"]}]}]

    async def company_alerts(engine, symbol, since, limit):
        calls["company"] = symbol
        return [{**ALERT, "tier": 1, "matched": ["NVDA"]}]

    monkeypatch.setattr(alerts.db, "recent_alerts", recent_alerts)
    monkeypatch.setattr(alerts.db, "company_alerts", company_alerts)
    app.dependency_overrides[alerts.get_db_engine] = lambda: None
    test_client = TestClient(app)
    test_client.calls = calls
    yield test_client
    app.dependency_overrides.clear()


def test_recent_alerts_carry_their_companies(client):
    body = client.get("/alerts/recent", params={"minutes": 30, "companies": 5}).json()
    assert body[0]["companies"][0] == {"symbol": "NVDA", "name": "NVIDIA CORP", "tier": 1, "matched": ["NVDA"],
                                       "relevance": None}
    assert client.calls["recent"] == (50, 5)


def test_company_alerts(client):
    body = client.get("/entities/NVDA/alerts").json()
    assert body[0]["tier"] == 1 and body[0]["market_id"] == "KXB200-1" and client.calls["company"] == "NVDA"
