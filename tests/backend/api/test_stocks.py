from fastapi.testclient import TestClient

from backend.api.deps import get_alpaca_gateway
from backend.ingestion.alpaca.models import AlpacaApiError
from backend.main import app


class FakeGateway:
    def __init__(self, bars=None, error=None):
        self._bars = bars or []
        self._error = error

    def fetch_bars(self, ticker, timeframe, start, end):
        if self._error:
            raise self._error
        return self._bars


def test_get_prices_returns_flattened_points():
    bars = [{"t": "2026-10-09T20:10:00Z", "c": 229.47, "v": 188}]
    app.dependency_overrides[get_alpaca_gateway] = lambda: FakeGateway(bars=bars)
    client = TestClient(app)
    try:
        resp = client.get("/stocks/NVDA/prices", params={"tier": "daily"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["market_id"] == "NVDA"
    assert data[0]["price_or_odds"] == 229.47
    assert "open" not in data[0]  # flat shape, not OHLC


def test_get_prices_defaults_to_daily_tier():
    app.dependency_overrides[get_alpaca_gateway] = lambda: FakeGateway(bars=[])
    client = TestClient(app)
    try:
        resp = client.get("/stocks/NVDA/prices")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_prices_maps_retryable_error_to_502():
    error = AlpacaApiError(code="429", message="too many requests", retryable=True)
    app.dependency_overrides[get_alpaca_gateway] = lambda: FakeGateway(error=error)
    client = TestClient(app)
    try:
        resp = client.get("/stocks/NVDA/prices", params={"tier": "daily"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 502


def test_get_prices_maps_non_retryable_error_to_400():
    error = AlpacaApiError(code="404", message="unknown symbol", retryable=False)
    app.dependency_overrides[get_alpaca_gateway] = lambda: FakeGateway(error=error)
    client = TestClient(app)
    try:
        resp = client.get("/stocks/BADTICKER/prices", params={"tier": "daily"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 400
