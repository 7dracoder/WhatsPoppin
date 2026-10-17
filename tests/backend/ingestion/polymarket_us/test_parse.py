import json
from datetime import datetime, timezone
from decimal import Decimal

from alert_detector.signals import is_good_quote
from backend.ingestion.common.db import PRICE_COLUMNS, TRADE_COLUMNS
from backend.ingestion.polymarket_us.polymarket_us import MarketSession, market_rows, parse_time, price_row, trade_row

NOW = 1_800_000_000.0
SLUG = "paccc-usse-midterms-2026-11-03-dem"


def px(v):
    return {"value": v, "currency": "USD"}


def market_data(bids, offers, ts="2026-10-10T15:30:05.353888256Z", last="0.5930"):
    return {"marketSlug": SLUG, "bids": [{"px": px(p), "qty": q} for p, q in bids],
            "offers": [{"px": px(p), "qty": q} for p, q in offers], "state": "MARKET_STATE_OPEN",
            "stats": {"lastTradePx": px(last), "sharesTraded": "24615626.0000", "openInterest": "398244.0000"},
            "transactTime": ts}


def trade(side, price="0.4900", qty="37.5000"):
    return {"marketSlug": SLUG, "price": px(price), "quantity": px(qty), "id": "D0X2X5JVTZ6Y",
            "tradeTime": "2026-10-10T15:30:08.414797335Z", "taker": {"side": side}, "maker": {}}


def test_nanosecond_timestamps_parse():
    assert parse_time("2026-10-10T15:30:08.414797335Z") == datetime(2026, 10, 10, 15, 30, 8, 414797,
                                                                     tzinfo=timezone.utc)
    assert parse_time("not a date") is None


def test_market_rows_map_fields():
    event = {"id": "149874", "slug": "senate-2026", "title": "U.S Senate Midterm Winner", "seriesSlug": "midterms",
             "category": "politics", "tags": [{"slug": "politics", "label": "Politics"}],
             "markets": [{"slug": SLUG, "question": "U.S Senate Midterm Winner", "title": "Democratic Party",
                          "description": "Rules", "endDate": "2027-02-02T04:59:00Z", "active": True, "closed": False},
                         {"slug": "closed-one", "endDate": "2027-02-02T04:59:00Z", "active": True, "closed": True}]}
    [row] = market_rows([event], ["politics"], 10, NOW)
    assert row["market_id"] == SLUG and row["outcome_label"] == "Democratic Party"
    assert row["category"] == "Politics" and row["url"] == "https://polymarket.us/event/senate-2026"


def test_market_data_gives_top_of_book_and_stats():
    md = market_data([("0.5910", "2738"), ("0.5920", "13095")], [("0.5930", "410"), ("0.5940", "9")])
    row = dict(zip(PRICE_COLUMNS, price_row(md, snapshot=False)))
    assert (row["yes_bid"], row["yes_bid_size"]) == (Decimal("0.5920"), Decimal("13095"))
    assert (row["yes_ask"], row["yes_ask_size"]) == (Decimal("0.5930"), Decimal("410"))
    assert row["volume"] == Decimal("24615626.0000") and row["price_or_odds"] == Decimal("0.5930")


def test_empty_side_is_size_zero_and_rejected():
    row = dict(zip(PRICE_COLUMNS, price_row(market_data([], [("0.10", "5")]), snapshot=False)))
    assert row["yes_bid"] == 0 and row["yes_bid_size"] == 0
    assert not is_good_quote(row["yes_bid"], row["yes_ask"], row["yes_bid_size"], row["yes_ask_size"], 0.10)


def test_taker_side_maps_to_yes_and_no():
    buy = dict(zip(TRADE_COLUMNS, trade_row(trade("ORDER_SIDE_BUY"))))
    sell = dict(zip(TRADE_COLUMNS, trade_row(trade("ORDER_SIDE_SELL"))))
    assert (buy["taker_side"], sell["taker_side"]) == ("yes", "no")
    assert buy["yes_price"] == Decimal("0.4900") and buy["count"] == Decimal("37.5000")
    assert trade_row(trade("ORDER_SIDE_UNSPECIFIED")) is None


def test_session_snapshot_then_only_top_changes():
    rows, trades = [], []
    s = MarketSession("k", None, None, rows.append, trades.append)
    s.followed, s.awaiting_snapshot = {SLUG}, {SLUG}
    book = market_data([("0.59", "10")], [("0.60", "5")])
    s.handle(json.dumps({"marketData": book}))
    s.handle(json.dumps({"marketData": book | {"transactTime": "2026-10-10T15:31:00Z"}}))  # same top: no row
    s.handle(json.dumps({"marketData": market_data([("0.59", "11")], [("0.60", "5")])}))  # size changed
    assert [dict(zip(PRICE_COLUMNS, r))["snapshot"] for r in rows] == [True, False]


def test_malformed_and_unfollowed_messages_are_skipped():
    rows, trades = [], []
    s = MarketSession("k", None, None, rows.append, trades.append)
    s.followed = {SLUG}
    s.handle("{not json")
    s.handle(json.dumps({"heartbeat": {}}))
    s.handle(json.dumps({"trade": {"marketSlug": SLUG, "taker": {"side": "ORDER_SIDE_BUY"}}}))  # missing fields
    s.handle(json.dumps({"marketData": market_data([("0.5", "1")], [("0.6", "1")]) | {"marketSlug": "other"}}))
    assert rows == [] and trades == []
