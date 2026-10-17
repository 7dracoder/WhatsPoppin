import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from backend.ingestion.kalshi.kalshi import PollingSession, parse_rest_market, parse_rest_trade

TS = datetime(2026, 10, 17, tzinfo=timezone.utc)


def _quote(ticker, last="0.40", bid="0.39", ask="0.41"):
    return {"ticker": ticker, "last_price_dollars": last, "yes_bid_dollars": bid, "yes_ask_dollars": ask,
            "yes_bid_size_fp": "10.00", "yes_ask_size_fp": "20.00", "volume_fp": "100.00", "open_interest_fp": "50.00"}


def _trade(ticker, trade_id, side="yes"):
    return {"ticker": ticker, "trade_id": trade_id, "created_time": "2026-10-17T12:00:00.5Z", "yes_price_dollars": "0.42",
            "count_fp": "3.00", "taker_outcome_side": side, "is_block_trade": False}


def test_parse_rest_market_matches_price_columns():
    row = parse_rest_market(_quote("FED-T4"), TS, snapshot=True)
    assert row == ("kalshi", "FED-T4", TS, Decimal("0.40"), Decimal("0.39"), Decimal("0.41"), Decimal("10.00"),
                   Decimal("20.00"), Decimal("100.00"), Decimal("50.00"), True)
    assert parse_rest_market({"ticker": "FED-T4"}, TS, snapshot=False) is None


def test_parse_rest_trade_reads_ticker_and_created_time():
    row = parse_rest_trade(_trade("FED-T4", "t1"))
    assert row[:4] == ("kalshi", "FED-T4", datetime(2026, 10, 17, 12, 0, 0, 500000, tzinfo=timezone.utc), "t1")
    assert row[4:] == (Decimal("0.42"), Decimal("3.00"), "yes", False)
    assert parse_rest_trade({**_trade("FED-T4", "t1"), "created_time": None}) is None


def _session(responses: list[dict]):
    """A PollingSession whose client returns `responses` per path, in order."""
    queues = {path: list(items) for path, items in responses}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=queues[request.url.path].pop(0))

    client = httpx.AsyncClient(base_url="https://kalshi.test", transport=httpx.MockTransport(handler))
    prices, trades = [], []
    return PollingSession(client, None, prices.append, trades.append), prices, trades


def test_prices_written_only_when_the_quote_changes():
    session, prices, _ = _session([("/markets", [
        {"markets": [_quote("A"), _quote("B")]},
        {"markets": [_quote("A"), _quote("B", bid="0.38")]},
    ])])
    asyncio.run(session._poll_prices(["A", "B"]))
    asyncio.run(session._poll_prices(["A", "B"]))
    assert [(r[1], r[-1]) for r in prices] == [("A", True), ("B", True), ("B", False)]


def test_trades_filtered_to_followed_markets_and_not_repeated():
    session, _, trades = _session([("/markets/trades", [
        {"trades": [_trade("A", "t1"), _trade("OTHER", "t2")]},
        {"trades": [_trade("A", "t1"), _trade("A", "t3")]},
    ])])
    asyncio.run(session._poll_trades({"A"}, 0))
    asyncio.run(session._poll_trades({"A"}, 0))
    assert [r[3] for r in trades] == ["t1", "t3"]
