import json
from decimal import Decimal

from alert_detector.signals import is_good_quote
from backend.ingestion.common.db import PRICE_COLUMNS, TRADE_COLUMNS
from backend.ingestion.polymarket.polymarket import (Book, MarketInfo, MarketSession, market_rows, parse_frame,
                                             price_row, trade_row)

NOW = 1_800_000_000.0
CID = "0xabc"
T0, T1 = "111", "222"

EVENT = {
    "id": 42, "slug": "fed-decision-in-december", "title": "Fed decision in December?",
    "seriesSlug": "fed-decisions",
    "tags": [{"slug": "politics", "label": "Politics"}, {"slug": "economy", "label": "Economy"}],
    "markets": [{
        "conditionId": CID, "question": "Will the Fed cut 25 bps?", "description": "Resolves YES if ...",
        "groupItemTitle": "25 bps cut", "outcomes": "[\"Yes\", \"No\"]",
        "clobTokenIds": json.dumps([T0, T1]), "endDate": "2027-12-10T19:00:00Z",
        "acceptingOrders": True, "enableOrderBook": True, "closed": False,
        "lastTradePrice": 0.42, "volume24hr": 1234.5,
    }],
}


def session_with_market(rows: list, trades: list) -> MarketSession:
    s = MarketSession(None, rows.append, trades.append, None)
    s.markets[CID] = MarketInfo((T0, T1), Decimal("0.42"))
    s.last_price[CID] = Decimal("0.42")
    s.tokens = {T0: (CID, 0), T1: (CID, 1)}
    s.books = {T0: Book(), T1: Book()}
    s.awaiting_snapshot = {T0}
    return s


def book_msg(token, bids, asks, ts="1800000000000"):
    return {"event_type": "book", "market": CID, "asset_id": token, "timestamp": ts,
            "bids": [{"price": p, "size": s} for p, s in bids], "asks": [{"price": p, "size": s} for p, s in asks]}


def test_json_string_fields_parse():
    [row] = market_rows([EVENT], ["economy", "politics"], 10, NOW)
    assert row["info"].tokens == (T0, T1)
    assert row["outcome_label"] == "25 bps cut"
    assert row["category"] == "Economy"  # first configured tag the event has
    assert row["event_id"] == "42" and row["url"] == "https://polymarket.com/event/fed-decision-in-december"


def test_book_gives_top_sizes():
    book = Book()
    book.replace([{"price": "0.40", "size": "100"}, {"price": "0.41", "size": "25"}],
                 [{"price": "0.43", "size": "70"}])
    row = dict(zip(PRICE_COLUMNS, price_row(CID, 1, book, "0.41", "0.43", None, False)))
    assert (row["yes_bid"], row["yes_bid_size"], row["yes_ask_size"]) == (Decimal("0.41"), 25, 70)
    assert row["price_or_odds"] == Decimal("0.42")  # no trade yet -> mid


def test_price_change_size_zero_removes_level():
    book = Book()
    book.replace([{"price": "0.40", "size": "100"}], [])
    book.update("BUY", "0.40", "0")
    book.update("SELL", "0.45", "12")
    assert book.bids == {} and book.asks == {Decimal("0.45"): Decimal("12")}


def test_empty_side_is_size_zero_and_rejected():
    book = Book()
    book.replace([], [{"price": "0.45", "size": "12"}])
    row = dict(zip(PRICE_COLUMNS, price_row(CID, 1, book, "0", "0.45", Decimal("0.44"), False)))
    assert row["yes_bid"] == 0 and row["yes_bid_size"] == 0
    assert not is_good_quote(row["yes_bid"], row["yes_ask"], row["yes_bid_size"], row["yes_ask_size"], 0.10)


def test_trade_on_outcome_1_converts_to_yes_view():
    msg = {"price": "0.30", "size": "50", "side": "BUY", "timestamp": "1800000000000",
           "transaction_hash": "0xt", "asset_id": T1}
    row = dict(zip(TRADE_COLUMNS, trade_row(CID, 1, msg)))
    assert row["yes_price"] == Decimal("0.70") and row["taker_side"] == "no"
    sold = dict(zip(TRADE_COLUMNS, trade_row(CID, 1, {**msg, "side": "SELL"})))
    assert sold["taker_side"] == "yes"
    assert dict(zip(TRADE_COLUMNS, trade_row(CID, 0, msg)))["taker_side"] == "yes"


def test_array_and_object_frames_parse():
    obj = {"event_type": "best_bid_ask", "asset_id": T0}
    assert parse_frame(json.dumps(obj)) == [obj]
    assert parse_frame(json.dumps([obj, obj])) == [obj, obj]


def test_pong_is_ignored():
    rows, trades = [], []
    s = session_with_market(rows, trades)
    assert parse_frame("PONG") == []
    s.handle("PONG")
    assert rows == [] and trades == []


def test_malformed_message_is_skipped():
    rows, trades = [], []
    s = session_with_market(rows, trades)
    s.handle("{not json")
    s.handle(json.dumps({"event_type": "last_trade_price", "asset_id": T0, "market": CID}))  # missing fields
    s.handle(json.dumps({"event_type": "book", "asset_id": T0, "bids": [{"price": "x"}]}))
    assert rows == [] and trades == []


def test_first_book_after_subscribe_is_snapshot():
    rows, trades = [], []
    s = session_with_market(rows, trades)
    s.handle(json.dumps([book_msg(T0, [("0.40", "10")], [("0.44", "5")]),
                         book_msg(T1, [("0.56", "5")], [("0.60", "10")])]))
    s.handle(json.dumps(book_msg(T0, [("0.41", "10")], [("0.44", "5")])))  # later book: no new row
    assert len(rows) == 1
    row = dict(zip(PRICE_COLUMNS, rows[0]))
    assert row["snapshot"] is True and row["yes_bid"] == Decimal("0.40")
