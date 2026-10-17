import pytest

from backend.api import market_activity as ma


@pytest.fixture(autouse=True)
def clear_cache():
    ma._cache.clear()


@pytest.mark.anyio
async def test_only_traded_keeps_markets_with_recent_prices(monkeypatch):
    async def fetch_candles(client, series, ticker, start_ts, end_ts, interval_min):
        return [{"price": {"close_dollars": "0.42"}}] if ticker == "KXA-1" else [{"price": {}}]

    async def poly_market(client, cid):
        return {"clobTokenIds": '["111", "222"]'}

    async def poly_history(client, token, start_ts, end_ts, interval_min):
        return [{"t": 1, "p": 0.6}] if token == "111" else []

    monkeypatch.setattr(ma, "fetch_candles", fetch_candles)
    monkeypatch.setattr(ma.polymarket, "fetch_market", poly_market)
    monkeypatch.setattr(ma.polymarket, "fetch_price_history", poly_history)
    rows = [{"source": "kalshi", "series_id": "KXA", "market_id": "KXA-1"},
            {"source": "kalshi", "series_id": "KXB", "market_id": "KXB-1"},  # a candle with no close: no trade
            {"source": "polymarket", "series_id": None, "market_id": "0xabc"}]
    kept = await ma.only_traded(rows, now=1_760_000_000)
    assert [m["market_id"] for m in kept] == ["KXA-1", "0xabc"]
    assert ma._cache[("kalshi", "KXB-1")][1] is False


@pytest.mark.anyio
async def test_venue_errors_keep_the_market_and_are_not_cached(monkeypatch):
    import httpx

    async def fetch_candles(*args):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(ma, "fetch_candles", fetch_candles)
    kept = await ma.only_traded([{"source": "kalshi", "series_id": "KXA", "market_id": "KXA-1"}], now=1.0)
    assert len(kept) == 1 and ma._cache == {}


@pytest.mark.anyio
async def test_answers_are_cached_per_market(monkeypatch):
    calls = []

    async def fetch_candles(client, series, ticker, *rest):
        calls.append(ticker)
        return [{"price": {"close_dollars": "0.5"}}]

    monkeypatch.setattr(ma, "fetch_candles", fetch_candles)
    row = {"source": "kalshi", "series_id": "KXA", "market_id": "KXA-1"}
    await ma.only_traded([row], now=100.0)
    await ma.only_traded([row], now=200.0)
    assert calls == ["KXA-1"]
