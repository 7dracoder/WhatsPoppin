from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.api import market_history as mh
from backend.ingestion.alpaca import AlpacaApiError
from backend.main import app

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("age, expected", [
    (timedelta(hours=3), ["1D", "ALL"]),
    (timedelta(days=2), ["1D", "1W", "ALL"]),  # the spec's example
    (timedelta(days=20), ["1D", "1W", "1M", "ALL"]),
    (timedelta(days=400), ["1D", "1W", "1M", "6M", "1Y", "ALL"]),
])
def test_available_ranges(age, expected):
    assert mh.available_ranges(NOW - age, NOW) == expected


def test_window_covers_the_range_and_all_starts_at_open():
    opened = NOW - timedelta(days=2)
    assert mh.window("1W", opened, NOW) == (NOW - timedelta(days=7), NOW)
    assert mh.window("ALL", opened, NOW) == (opened, NOW)


def test_market_points_keep_only_real_closes_sorted():
    candles = [
        {"end_period_ts": 300, "price": {"close_dollars": "0.45"}, "volume_fp": "57.25"},
        {"end_period_ts": 100, "price": {"close_dollars": "0.40"}, "volume": 12},  # older API: int volume
        {"end_period_ts": 200, "price": {"close_dollars": None}},  # nothing traded: left out, not filled
        {"end_period_ts": 250, "price": {}},
        {"end_period_ts": 350, "price": {"close_dollars": "0.46"}},  # no volume field at all
    ]
    assert [(p.t, p.close, p.volume) for p in mh.market_points(candles)] == [
        (100, 0.40, 12.0), (300, 0.45, 57.25), (350, 0.46, None)]


def test_stock_points_are_stamped_at_the_bar_end_with_share_volume():
    bars = [{"t": "2026-10-09T14:30:00Z", "c": 251.3, "v": 10}, {"t": "2026-10-09T14:31:00Z"}]  # 2nd: no close
    (point,) = mh.stock_points(bars, "TSLA", 1)
    assert point.t == int(datetime(2026, 10, 9, 14, 31, tzinfo=timezone.utc).timestamp()) and point.close == 251.3
    assert point.volume == 10


class FakeAlpaca:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def fetch_bars(self, ticker, timeframe, start, end):
        self.calls.append((ticker, timeframe, start, end))
        if self.error:
            raise self.error
        return [{"t": "2026-10-09T15:00:00Z", "c": 250.0, "v": 1200}]


@pytest.fixture
def client(monkeypatch):
    calls = {}

    async def venue(engine, market_id):
        return {"KXWC-30-ESP": ("kalshi", "KXWC"), "0xabc": ("polymarket", None)}.get(market_id)

    async def poly_market(client, condition_id):
        return {"startDate": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                "clobTokenIds": '["111", "222"]'}

    async def poly_history(client, token, start_ts, end_ts, interval_min):
        calls["poly"] = (token, start_ts, end_ts, interval_min)
        return [{"t": 1760000000, "p": 0.61}]

    async def fetch_market(client, ticker):
        return {"open_time": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}

    async def fetch_candles(client, series, ticker, start_ts, end_ts, interval_min):
        calls["candles"] = (series, ticker, start_ts, end_ts, interval_min)
        return [{"end_period_ts": 1760000000, "price": {"close_dollars": "0.42"}, "volume_fp": "57.25"}]

    monkeypatch.setattr(mh, "_venue", venue)
    monkeypatch.setattr(mh.polymarket, "fetch_market", poly_market)
    monkeypatch.setattr(mh.polymarket, "fetch_price_history", poly_history)
    monkeypatch.setattr(mh, "fetch_market", fetch_market)
    monkeypatch.setattr(mh, "fetch_candles", fetch_candles)
    alpaca = FakeAlpaca()
    app.dependency_overrides[mh.get_db_engine] = lambda: None
    app.dependency_overrides[mh.get_alpaca_gateway] = lambda: alpaca
    test_client = TestClient(app)
    test_client.calls, test_client.alpaca = calls, alpaca
    yield test_client
    app.dependency_overrides.clear()


def test_history_returns_both_series_at_the_range_interval(client):
    body = client.get("/markets/KXWC-30-ESP/history", params={"stock": "tsla", "range": "1W"}).json()
    assert body["interval"] == "1h" and body["stock_ticker"] == "TSLA"
    assert body["available_ranges"] == ["1D", "1W", "ALL"]
    assert body["market"] == [{"t": 1760000000, "close": 0.42, "volume": 57.25}]
    assert datetime.now(timezone.utc) - datetime.fromisoformat(body["open_time"]) < timedelta(days=3)
    assert body["stock"][0]["close"] == 250.0 and body["stock"][0]["volume"] == 1200
    series, ticker, start_ts, end_ts, interval = client.calls["candles"]
    assert (series, ticker, interval) == ("KXWC", "KXWC-30-ESP", 60)
    assert end_ts - start_ts < 3 * 86400  # a 2-day-old market's 1W series starts at its open time
    ticker_, timeframe, start, end = client.alpaca.calls[0]
    assert (ticker_, timeframe) == ("TSLA", "1Hour") and (end - start) == timedelta(days=7)  # stock: whole week


def test_polymarket_history_uses_the_yes_token(client):
    body = client.get("/markets/0xabc/history", params={"stock": "nvda", "range": "1W"}).json()
    # Polymarket's price history has no volume: null, not 0, so the chart knows it is unavailable.
    assert body["market"] == [{"t": 1760000000, "close": 0.61, "volume": None}] and body["interval"] == "1h"
    assert body["available_ranges"] == ["1D", "1W", "ALL"]
    token, start_ts, end_ts, interval = client.calls["poly"]
    assert (token, interval) == ("111", 60)  # outcome 0 is our YES
    assert end_ts - start_ts < 3 * 86400  # from the market's start date, not the whole week


def test_range_longer_than_the_market_is_rejected(client):
    response = client.get("/markets/KXWC-30-ESP/history", params={"stock": "TSLA", "range": "1M"})
    assert response.status_code == 400 and "1D, 1W, ALL" in response.json()["detail"]


def test_outcomes_lists_the_event_siblings(monkeypatch):
    from backend.api import market_history as mh_

    class Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            sql = str(stmt)
            class R:
                def first(self_):
                    return ("kalshi", "KXNVDAMENTION-26OCT21")
                def all(self_):
                    assert "markets.event_id" in sql and "markets.status" in sql
                    row = {"source": "kalshi", "market_id": "KXNVDAMENTION-26OCT21-TSM", "title": "t",
                           "outcome_label": "TSM", "event_title": "What will Jensen say?", "rules_primary": None,
                           "close_time": None, "url": None}
                    class M:
                        _mapping = row
                    return [M()]
            return R()

    class Engine:
        def connect(self):
            return Conn()

    app.dependency_overrides[mh_.get_db_engine] = lambda: Engine()
    try:
        body = TestClient(app).get("/markets/KXNVDAMENTION-26OCT21-AI/outcomes").json()
    finally:
        app.dependency_overrides.clear()
    assert [m["outcome_label"] for m in body] == ["TSM"]


def test_unknown_market_is_404(client):
    assert client.get("/markets/NOPE/history", params={"stock": "TSLA"}).status_code == 404


def test_alpaca_error_is_reported(client):
    client.alpaca.error = AlpacaApiError("not_found", "unknown symbol", retryable=False)
    response = client.get("/markets/KXWC-30-ESP/history", params={"stock": "ZZZZ", "range": "1D"})
    assert response.status_code == 400 and response.json()["detail"] == "unknown symbol"
