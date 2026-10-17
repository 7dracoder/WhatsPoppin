import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.ingestion.alpaca.client import AlpacaApiGateway
from backend.ingestion.alpaca.models import AlpacaApiError

FIXTURES = Path(__file__).parent / "fixtures"
START = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeHttpxClient:
    """Stands in for httpx.Client. `responses` maps a path to either one
    FakeResponse, or a list consumed in order (for pagination).
    """

    def __init__(self, responses: dict[str, FakeResponse | list[FakeResponse]]):
        self._responses = {k: (v if isinstance(v, list) else [v]) for k, v in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        queue = self._responses.get(path)
        if not queue:
            raise AssertionError(f"no fake response configured for GET {path}")
        return queue.pop(0) if len(queue) > 1 else queue[0]


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_fetch_bars_returns_bars():
    fake = FakeHttpxClient({"/stocks/NVDA/bars": FakeResponse(200, _load("sample_bars_response.json"))})
    gateway = AlpacaApiGateway(key_id="dummy", secret_key="dummy", http_client=fake)

    bars = gateway.fetch_bars("NVDA", "1Min", START, END)

    assert len(bars) == 3
    assert fake.calls[0][1]["timeframe"] == "1Min"
    assert fake.calls[0][1]["feed"] == "iex"


def test_fetch_bars_follows_pagination():
    fake = FakeHttpxClient({
        "/stocks/NVDA/bars": [
            FakeResponse(200, _load("sample_bars_page1.json")),
            FakeResponse(200, _load("sample_bars_page2.json")),
        ],
    })
    gateway = AlpacaApiGateway(key_id="dummy", secret_key="dummy", http_client=fake)

    bars = gateway.fetch_bars("NVDA", "1Hour", START, END)

    assert len(bars) == 2
    assert len(fake.calls) == 2
    assert "page_token" not in fake.calls[0][1]
    assert fake.calls[1][1]["page_token"] == "page2"


def test_fetch_bars_raises_retryable_on_429():
    fake = FakeHttpxClient({"/stocks/NVDA/bars": FakeResponse(429, {"message": "too many requests"})})
    gateway = AlpacaApiGateway(key_id="dummy", secret_key="dummy", http_client=fake)

    with pytest.raises(AlpacaApiError) as exc_info:
        gateway.fetch_bars("NVDA", "1Min", START, END)

    assert exc_info.value.code == "429"
    assert exc_info.value.retryable is True


def test_fetch_bars_raises_non_retryable_on_401():
    fake = FakeHttpxClient({"/stocks/NVDA/bars": FakeResponse(401, {"message": "unauthorized"})})
    gateway = AlpacaApiGateway(key_id="dummy", secret_key="dummy", http_client=fake)

    with pytest.raises(AlpacaApiError) as exc_info:
        gateway.fetch_bars("NVDA", "1Min", START, END)

    assert exc_info.value.code == "401"
    assert exc_info.value.retryable is False
