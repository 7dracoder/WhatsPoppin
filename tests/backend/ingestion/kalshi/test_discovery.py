import asyncio
from datetime import datetime, timezone

import httpx

from backend.ingestion.kalshi import kalshi
from backend.ingestion.kalshi.kalshi import discover_categories, market_row, top_by_volume

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc).timestamp()


def _market(ticker, event, volume="0", status="active", close="2026-12-31T00:00:00Z"):
    return {"ticker": ticker, "event_ticker": event, "title": f"title {ticker}", "yes_sub_title": f"yes {ticker}",
            "rules_primary": "rules", "close_time": close, "status": status, "volume_24h_fp": volume}


EVENTS = [
    {"event_ticker": "FED-26DEC", "series_ticker": "KXFED", "category": "Economics", "title": "Fed in December?",
     "markets": [_market("FED-26DEC-T3.75", "FED-26DEC", "500.00"), _market("FED-26DEC-T4.00", "FED-26DEC", "20.00"),
                 _market("FED-26DEC-OLD", "FED-26DEC", status="finalized")]},
    {"event_ticker": "BTC-26OCT16", "series_ticker": "KXBTCD", "category": "Crypto", "title": "BTC on Oct 16?",
     "markets": [_market("BTC-T70", "BTC-26OCT16", "9999.00")]},
    {"event_ticker": "HIGHNY-26OCT11", "series_ticker": "KXHIGHNY", "category": "Climate and Weather",
     "title": "NYC high?", "markets": [_market("HIGHNY-B62", "HIGHNY-26OCT11", "5.00")]},
]
SERIES = {"KXFED": {"ticker": "KXFED", "title": "Fed decision", "category": "Economics", "tags": ["Fed"]},
          "KXHIGHNY": {"ticker": "KXHIGHNY", "title": "Highest temperature in NYC", "tags": None}}


def _client(requests: list, rate_limit_first: int = 0) -> httpx.AsyncClient:
    state = {"limited": rate_limit_first}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if state["limited"]:
            state["limited"] -= 1
            return httpx.Response(429)
        path, params = request.url.path, request.url.params
        if path == "/events":  # two pages, to exercise the cursor
            if params.get("cursor"):
                return httpx.Response(200, json={"events": EVENTS[2:], "cursor": ""})
            return httpx.Response(200, json={"events": EVENTS[:2], "cursor": "page2"})
        if path == "/series":
            return httpx.Response(200, json={"series": [s for s in SERIES.values()
                                                        if s.get("category") == params["category"]]})
        if path.startswith("/series/"):
            return httpx.Response(200, json={"series": SERIES[path.rsplit("/", 1)[1]]})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://kalshi.test")


def _no_sleep(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr(kalshi.asyncio, "sleep", instant)


def test_discovers_active_markets_of_wanted_categories_only(monkeypatch):
    _no_sleep(monkeypatch)
    rows = asyncio.run(discover_categories(_client([]), ["Economics"], []))
    assert [r["market_id"] for r in rows] == ["FED-26DEC-T3.75", "FED-26DEC-T4.00"]  # no crypto, no finalized
    fed = rows[0]
    assert fed["event_id"] == "FED-26DEC" and fed["event_title"] == "Fed in December?"
    assert fed["series_id"] == "KXFED" and fed["series_title"] == "Fed decision" and fed["tags"] == ["Fed"]
    assert fed["category"] == "Economics" and fed["volume_24h"] == 500.0
    assert fed["url"] == "https://kalshi.com/markets/kxfed"


def test_listed_series_are_added_on_top_of_categories(monkeypatch):
    _no_sleep(monkeypatch)
    rows = asyncio.run(discover_categories(_client([]), ["Economics"], ["KXHIGHNY"]))
    weather = next(r for r in rows if r["market_id"] == "HIGHNY-B62")
    assert weather["series_title"] == "Highest temperature in NYC" and weather["tags"] == []


def test_rate_limited_requests_are_retried(monkeypatch):
    _no_sleep(monkeypatch)
    requests = []
    rows = asyncio.run(discover_categories(_client(requests, rate_limit_first=2), ["Economics"], []))
    assert len(rows) == 2
    assert len(requests) == 2 + 2 + 1  # two 429s, two event pages, one series list


def test_top_by_volume_streams_the_most_traded_open_markets():
    rows = [market_row(_market(t, "E", v, close=c), {}, "S", {}) for t, v, c in [
        ("A", "10", "2026-12-31T00:00:00Z"), ("B", "300", "2026-12-31T00:00:00Z"),
        ("C", "200", "2026-12-31T00:00:00Z"), ("CLOSED", "9999", "2026-01-01T00:00:00Z")]]
    assert top_by_volume(rows, 2, NOW) == {"B", "C"}
    assert top_by_volume(rows, 10, NOW) == {"A", "B", "C"}


def test_missing_or_bad_volume_counts_as_zero():
    assert market_row({"ticker": "X", "volume_24h_fp": "n/a"}, {}, "S", {})["volume_24h"] == 0.0
    assert market_row({"ticker": "X"}, {}, "S", {})["volume_24h"] == 0.0
