import json
from datetime import datetime, timezone
from pathlib import Path

from backend.ingestion.alpaca.client import AlpacaApiGateway
from backend.ingestion.alpaca.models import ZoomTier
from backend.ingestion.alpaca.service import fetch_price_series

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code: int, json_data: dict):
        self.status_code = status_code
        self._json = json_data
        self.text = ""

    def json(self):
        return self._json


class FakeHttpxClient:
    def __init__(self, response: FakeResponse):
        self._response = response
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        return self._response


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_fetch_price_series_end_to_end_flattens_to_close():
    fake = FakeHttpxClient(FakeResponse(200, _load("sample_bars_response.json")))
    gateway = AlpacaApiGateway(key_id="dummy", secret_key="dummy", http_client=fake)

    points = fetch_price_series("NVDA", ZoomTier.RECENT, gateway, now=NOW)

    # fixture's third bar is malformed (no close) and dropped by normalize_batch
    assert len(points) == 2
    assert points[0].price_or_odds == 183.0
    assert points[0].source == "alpaca"
    assert points[0].market_id == "NVDA"


def test_fetch_price_series_uses_correct_tier_params():
    fake = FakeHttpxClient(FakeResponse(200, _load("sample_bars_response.json")))
    gateway = AlpacaApiGateway(key_id="dummy", secret_key="dummy", http_client=fake)

    fetch_price_series("NVDA", ZoomTier.WEEKLY, gateway, now=NOW)

    assert fake.calls[0][1]["timeframe"] == "5Min"
    assert fake.calls[0][1]["end"] == "2026-10-10T15:00:00Z"
