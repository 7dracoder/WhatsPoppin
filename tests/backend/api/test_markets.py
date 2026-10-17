from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.deps import get_kalshi_client
from backend.api.markets import RANGE_CONFIG, candles_to_points, mock_series, router

app = FastAPI()
app.include_router(router)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def _client_with(handler):
    app.dependency_overrides[get_kalshi_client] = lambda: httpx.Client(
        base_url="https://kalshi.test", transport=httpx.MockTransport(handler)
    )
    return TestClient(app)


def _fail(request):
    return httpx.Response(500)


def test_range_to_interval_mapping():
    assert RANGE_CONFIG["1w"][1] == 60
    assert RANGE_CONFIG["1m"][1] == 60
    assert RANGE_CONFIG["3m"][1] == 1440
    assert RANGE_CONFIG["3m"][0].days == 90


def test_route_without_mock_is_404_when_kalshi_has_nothing():
    client = _client_with(_fail)
    try:
        response = client.get("/markets/KXFED-26DEC-T4/series", params={"range": "1w", "mock": "false"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404


def test_route_falls_back_to_deterministic_mock():
    client = _client_with(_fail)
    try:
        a = client.get("/markets/KXFED-26DEC-T4/series", params={"range": "1w"}).json()
        b = client.get("/markets/KXFED-26DEC-T4/series", params={"range": "1w"}).json()
    finally:
        app.dependency_overrides.clear()
    assert a and set(a[0]) == {"source", "market_id", "price_or_odds", "timestamp"}
    assert a[0]["source"] == "kalshi" and a[0]["market_id"] == "KXFED-26DEC-T4"
    assert all(1 <= p["price_or_odds"] <= 99 for p in a)
    assert [p["price_or_odds"] for p in a] == [p["price_or_odds"] for p in b]


def test_route_rejects_unknown_range():
    client = _client_with(_fail)
    try:
        assert client.get("/markets/X-1/series", params={"range": "5y"}).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_route_uses_kalshi_candles_and_requested_interval():
    seen = {}

    def handler(request):
        seen["path"], seen["params"] = request.url.path, dict(request.url.params)
        return httpx.Response(200, json={"candlesticks": [
            {"end_period_ts": 1760000000, "price": {"close_dollars": "0.42"}},
            {"end_period_ts": 1760003600, "price": {"close_dollars": None},
             "yes_bid": {"close_dollars": "0.40"}, "yes_ask": {"close_dollars": "0.44"}},
        ]})

    client = _client_with(handler)
    try:
        data = client.get("/markets/KXFED-26DEC-T4/series", params={"range": "3m"}).json()
    finally:
        app.dependency_overrides.clear()
    assert seen["path"].endswith("/series/KXFED/markets/KXFED-26DEC-T4/candlesticks")
    assert seen["params"]["period_interval"] == "1440"
    assert [p["price_or_odds"] for p in data] == [42.0, 42.0]


def test_candles_without_any_price_are_skipped():
    assert candles_to_points("M", [{"end_period_ts": 1, "price": {}}]) == []


def test_mock_series_is_deterministic_and_per_market():
    assert mock_series("A", "1m", NOW) == mock_series("A", "1m", NOW)
    assert mock_series("A", "1m", NOW) != mock_series("B", "1m", NOW)
