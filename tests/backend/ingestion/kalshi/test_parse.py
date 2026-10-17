from datetime import datetime, timezone
from decimal import Decimal

from backend.ingestion.common.db import PRICE_COLUMNS, TRADE_COLUMNS
from backend.ingestion.kalshi.kalshi import SnapshotMarker, parse_ticker, parse_trade

TICKER_MSG = {
    "market_ticker": "FED-23DEC-T3.00",
    "price_dollars": "0.4800",
    "yes_bid_dollars": "0.4500",
    "yes_ask_dollars": "0.5300",
    "volume_fp": "33896.00",
    "open_interest_fp": "20422.00",
    "yes_bid_size_fp": "300.00",
    "yes_ask_size_fp": "150.00",
    "ts_ms": 1669149841000,
}


def test_parses_normal_message():
    row = dict(zip(PRICE_COLUMNS, parse_ticker(TICKER_MSG, snapshot=False)))
    assert row["source"] == "kalshi"
    assert row["market_id"] == "FED-23DEC-T3.00"
    assert row["timestamp"] == datetime(2022, 11, 22, 20, 44, 1, tzinfo=timezone.utc)
    assert row["yes_bid"] == Decimal("0.4500")
    assert row["volume"] == Decimal("33896.00")
    assert row["snapshot"] is False


def test_missing_required_field_is_skipped():
    msg = {k: v for k, v in TICKER_MSG.items() if k != "yes_ask_dollars"}
    assert parse_ticker(msg, snapshot=False) is None


def test_garbage_value_is_skipped():
    assert parse_ticker({**TICKER_MSG, "price_dollars": "abc"}, snapshot=False) is None


def test_decimal_precision_is_exact():
    row = dict(zip(PRICE_COLUMNS, parse_ticker({**TICKER_MSG, "price_dollars": "0.1000"}, snapshot=False)))
    assert isinstance(row["price_or_odds"], Decimal)
    assert str(row["price_or_odds"]) == "0.1000"  # a float would give 0.1 or 0.1000000000000000055...


def test_first_ticker_after_subscribe_is_snapshot():
    marker = SnapshotMarker()
    marker.expect(["A", "B"])
    assert marker.is_snapshot("A") is True
    assert marker.is_snapshot("A") is False  # only the first one
    assert marker.is_snapshot("C") is False  # never subscribed/added
    marker.forget(["B"])
    assert marker.is_snapshot("B") is False  # removed before its snapshot arrived


TRADE_MSG = {
    "trade_id": "d91bc706-ee49-470d-82d8-11418bda6fed",
    "market_ticker": "HIGHNY-22DEC23-B53.5",
    "yes_price_dollars": "0.3600",
    "no_price_dollars": "0.6400",
    "count_fp": "136.00",
    "taker_side": "no",
    "taker_outcome_side": "no",
    "taker_book_side": "ask",
    "is_block_trade": False,
    "ts_ms": 1669149841000,
}


def test_parses_trade():
    row = dict(zip(TRADE_COLUMNS, parse_trade(TRADE_MSG)))
    assert row["market_id"] == "HIGHNY-22DEC23-B53.5"
    assert row["yes_price"] == Decimal("0.3600")
    assert row["count"] == Decimal("136.00")
    assert row["taker_side"] == "no"
    assert row["is_block_trade"] is False


def test_trade_falls_back_to_deprecated_taker_side():
    msg = {k: v for k, v in TRADE_MSG.items() if k != "taker_outcome_side"}
    assert dict(zip(TRADE_COLUMNS, parse_trade(msg)))["taker_side"] == "no"


def test_trade_missing_count_is_skipped():
    msg = {k: v for k, v in TRADE_MSG.items() if k != "count_fp"}
    assert parse_trade(msg) is None
