"""Polymarket (global) client: Gamma REST discovery, order-book tracking, WebSocket market stream.

Everything is stored from the point of view of outcome 0 (clobTokenIds[0]), our "YES".
For Yes/No markets that's "Yes"; for others, markets.outcome_label says what YES means.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx
import websockets

log = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
HISTORY_MAX_SPAN_S = 14 * 86400  # /prices-history rejects a startTs..endTs window much longer than this
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SOURCE = "polymarket"
DISCOVERY_INTERVAL_S = 5 * 60
HEARTBEAT_S = 10          # the server expects a text "PING" at least this often
PAGE = 100                # Gamma caps `limit` at 100
MAX_PAGES_PER_TAG = 5     # events come most-traded first; deeper pages are long-tail
SUBSCRIBE_CHUNK = 200     # one subscribe with ~1,000 tokens silently fails; 200 per message works
ZERO, ONE = Decimal(0), Decimal(1)


# --- Discovery ----------------------------------------------------------------------
@dataclass
class MarketInfo:
    tokens: tuple[str, str]       # (outcome 0 = our YES, outcome 1)
    last_price: Decimal | None    # last trade price of outcome 0, seeded from Gamma


def _json_list(value) -> list:
    """Gamma returns `outcomes` and `clobTokenIds` as JSON strings, e.g. '["Yes", "No"]'."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def _parse_time(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None
    except ValueError:
        return None


def market_rows(events: list[dict], tags: list[str], max_markets: int, now: float) -> list[dict]:
    """Open, tradable markets of these events -> `markets` rows (plus `info`), top N by 24h volume."""
    rows, seen = [], set()
    for ev in events:
        event_tags = {t.get("slug"): t.get("label") for t in ev.get("tags") or []}
        category = next((event_tags[t] for t in tags if t in event_tags), None)
        for m in ev.get("markets") or []:
            cid = m.get("conditionId")
            tokens = _json_list(m.get("clobTokenIds"))
            outcomes = _json_list(m.get("outcomes"))
            close = _parse_time(m.get("endDate"))
            if (not cid or cid in seen or len(tokens) != 2 or not m.get("acceptingOrders")
                    or not m.get("enableOrderBook") or m.get("closed")
                    or close is None or close.timestamp() <= now):
                continue
            seen.add(cid)
            last = m.get("lastTradePrice")
            rows.append({
                "market_id": cid,
                "title": m.get("question"),
                "outcome_label": m.get("groupItemTitle") or (outcomes[0] if outcomes else None),
                "rules_primary": m.get("description"),
                "event_id": str(ev.get("id")),
                "event_title": ev.get("title"),
                "series_id": ev.get("seriesSlug"),
                "series_title": None,
                "category": category,
                "tags": [label for label in event_tags.values() if label],
                "close_time": close,
                "url": f"https://polymarket.com/event/{ev.get('slug')}",
                "volume24hr": float(m.get("volume24hr") or 0),
                "info": MarketInfo((str(tokens[0]), str(tokens[1])),
                                   Decimal(str(last)) if last not in (None, "") else None),
            })
    rows.sort(key=lambda r: r["volume24hr"], reverse=True)
    return rows[:max_markets]


async def fetch_market(client: httpx.AsyncClient, condition_id: str) -> dict:
    """One market's Gamma record (`startDate`, `clobTokenIds`...), or {} when Gamma has none.
    Gamma leaves closed markets out unless asked, so a miss is retried with closed=true.
    """
    for extra in ({}, {"closed": "true"}):
        resp = await client.get(f"{GAMMA_URL}/markets", params={"condition_ids": condition_id, **extra})
        resp.raise_for_status()
        rows = resp.json()
        if rows:
            return rows[0]
    return {}


def yes_token(market: dict) -> str | None:
    """The CLOB token of outcome 0, our YES (see the module docstring)."""
    tokens = _json_list(market.get("clobTokenIds"))
    return str(tokens[0]) if tokens else None


async def fetch_price_history(client: httpx.AsyncClient, token: str, start_ts: int, end_ts: int,
                              interval_min: int) -> list[dict]:
    """Price points `{"t": unix seconds, "p": 0-1}` for one token in [start_ts, end_ts], about one
    per `interval_min` minutes, oldest first. Polymarket returns only moments it has a price for.

    The endpoint refuses a long startTs..endTs window, so minute and hour data is fetched in
    windows of HISTORY_MAX_SPAN_S. Daily data instead asks for the market's whole life
    (`interval=max`, one small response) and keeps the part inside the window.
    """
    async def get(params: dict) -> list[dict]:
        resp = await client.get(f"{CLOB_URL}/prices-history", params={"market": token, **params})
        resp.raise_for_status()
        return resp.json().get("history") or []

    if interval_min >= 1440:
        points = await get({"interval": "max", "fidelity": interval_min})
    else:
        points = []
        for start in range(start_ts, end_ts, HISTORY_MAX_SPAN_S):
            points += await get({"startTs": start, "endTs": min(start + HISTORY_MAX_SPAN_S, end_ts),
                                 "fidelity": interval_min})
    by_time = {int(p["t"]): p for p in points if start_ts <= int(p["t"]) <= end_ts}  # windows share an edge
    return [by_time[t] for t in sorted(by_time)]


async def fetch_events(client: httpx.AsyncClient, tag: str) -> list[dict]:
    events = []
    for page in range(MAX_PAGES_PER_TAG):
        resp = await client.get("/events", params={
            "tag_slug": tag, "active": "true", "closed": "false", "limit": PAGE,
            "offset": page * PAGE, "order": "volume24hr", "ascending": "false"})
        resp.raise_for_status()
        batch = resp.json()
        events += batch
        if len(batch) < PAGE:
            break
    return events


# --- Order book -----------------------------------------------------------------------
@dataclass
class Book:
    """Price -> size per side for one token. Only used to know the size at the best bid/ask."""
    bids: dict = field(default_factory=dict)
    asks: dict = field(default_factory=dict)

    def replace(self, bids: list, asks: list) -> None:
        self.bids = {Decimal(l["price"]): Decimal(l["size"]) for l in bids}
        self.asks = {Decimal(l["price"]): Decimal(l["size"]) for l in asks}

    def update(self, side: str, price: str, size: str) -> None:
        levels = self.bids if side == "BUY" else self.asks
        p, s = Decimal(price), Decimal(size)
        if s == 0:
            levels.pop(p, None)
        else:
            levels[p] = s

    def best(self) -> tuple:
        return (max(self.bids) if self.bids else None, min(self.asks) if self.asks else None)


def _dec(value) -> Decimal | None:
    """'' / None / '0' for an empty side -> None."""
    try:
        d = Decimal(value)
    except (TypeError, ValueError, InvalidOperation):
        return None
    return d if d > 0 else None


def _ts(ms) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


def price_row(cid: str, ts_ms, book: Book, best_bid, best_ask, last_price: Decimal | None,
              snapshot: bool) -> tuple | None:
    """market_prices row (see ingestion.common.db.PRICE_COLUMNS) for outcome 0, or None.

    An empty side is stored like Kalshi does: bid 0 / ask 1 with size 0, which the
    detector's good-quote rule rejects. We never invent a price.
    """
    bid, ask = _dec(best_bid), _dec(best_ask)
    bid_size = book.bids.get(bid, ZERO) if bid is not None else ZERO
    ask_size = book.asks.get(ask, ZERO) if ask is not None else ZERO
    if last_price is None:
        if bid is not None and ask is not None:
            last_price = (bid + ask) / 2
        else:
            last_price = bid or ask
    if last_price is None:
        return None  # empty book and no trade yet: nothing to say
    return (SOURCE, cid, _ts(ts_ms), last_price, bid if bid is not None else ZERO,
            ask if ask is not None else ONE, bid_size, ask_size, None, None, snapshot)


def trade_row(cid: str, outcome: int, msg: dict) -> tuple | None:
    """last_trade_price -> market_trades row (see ingestion.common.db.TRADE_COLUMNS), YES point of view.

    `side` is the taker's side on the token it traded (verified: BUY prints at the ask).
    A taker buying outcome 1 is a NO buyer at YES price 1 - p, and so on.
    """
    try:
        price, size, side = Decimal(msg["price"]), Decimal(msg["size"]), msg["side"]
        if side not in ("BUY", "SELL"):
            return None
        bought = side == "BUY"
        if outcome == 0:
            yes_price, taker = price, ("yes" if bought else "no")
        else:
            yes_price, taker = ONE - price, ("no" if bought else "yes")
        return (SOURCE, cid, _ts(msg["timestamp"]), f"{msg['transaction_hash']}:{msg['asset_id']}",
                yes_price, size, taker, False)
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


def parse_frame(raw) -> list[dict] | None:
    """A frame is a JSON object or an array of them; 'PONG' heartbeats -> []; garbage -> None."""
    if raw == "PONG":
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    items = data if isinstance(data, list) else [data]
    return [d for d in items if isinstance(d, dict)]


# --- WebSocket session ----------------------------------------------------------------------
class MarketSession:
    """One WebSocket connection: subscribe, keep the token list in sync, turn messages into rows.

    `discover` is an async callable returning {market_id: MarketInfo} to follow.
    `close_market` is an async callable(market_id) for markets that resolve.
    """

    def __init__(self, discover, on_price, on_trade, close_market):
        self.discover = discover
        self.on_price = on_price
        self.on_trade = on_trade
        self.close_market = close_market
        self.ws = None
        self.markets: dict[str, MarketInfo] = {}
        self.tokens: dict[str, tuple[str, int]] = {}  # token -> (market_id, outcome index)
        self.books: dict[str, Book] = {}
        self.last_price: dict[str, Decimal | None] = {}
        self.awaiting_snapshot: set[str] = set()  # outcome-0 tokens whose first book is a snapshot
        self.subscribed = False  # the first subscribe must be the full {"type": "market"} message

    async def run(self) -> None:
        """Returns or raises when the connection ends; the caller reconnects."""
        followed = await self.discover()
        # Polymarket's keepalive is our text PING (below); the library's protocol pings
        # can time out behind the flood of price_change messages, so turn them off.
        async with websockets.connect(WS_URL, max_size=None, ping_interval=None) as ws:
            self.ws = ws
            log.info("WebSocket connected")
            await self.sync(followed)
            helpers = [asyncio.create_task(self._heartbeat()), asyncio.create_task(self._refresh_loop())]
            try:
                async for raw in ws:
                    self.handle(raw)
            finally:
                for t in helpers:
                    t.cancel()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            await self.ws.send("PING")

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(DISCOVERY_INTERVAL_S)
            try:
                await self.sync(await self.discover())
            except (httpx.HTTPError, ValueError, KeyError) as e:
                log.warning("Discovery failed, keeping current markets: %s", e)

    async def sync(self, followed: dict[str, MarketInfo]) -> None:
        added = [m for m in followed if m not in self.markets]
        removed = [m for m in self.markets if m not in followed]
        for m in added:
            info = followed[m]
            self.markets[m] = info
            self.last_price[m] = info.last_price
            for i, token in enumerate(info.tokens):
                self.tokens[token] = (m, i)
                self.books[token] = Book()
            self.awaiting_snapshot.add(info.tokens[0])
        removed_tokens = []
        for m in removed:
            info = self.markets.pop(m)
            self.last_price.pop(m, None)
            for token in info.tokens:
                self.tokens.pop(token, None)
                self.books.pop(token, None)
                self.awaiting_snapshot.discard(token)
                removed_tokens.append(token)

        new_tokens = [t for m in added for t in followed[m].tokens]
        for i in range(0, len(new_tokens), SUBSCRIBE_CHUNK):
            chunk = new_tokens[i:i + SUBSCRIBE_CHUNK]
            if not self.subscribed:
                await self._send({"assets_ids": chunk, "type": "market", "custom_feature_enabled": True})
                self.subscribed = True
            else:
                # The flag must be on every subscribe: one message without it turns
                # best_bid_ask off for the whole connection (verified live).
                await self._send({"assets_ids": chunk, "operation": "subscribe", "custom_feature_enabled": True})
        for i in range(0, len(removed_tokens), SUBSCRIBE_CHUNK):
            await self._send({"assets_ids": removed_tokens[i:i + SUBSCRIBE_CHUNK], "operation": "unsubscribe"})
        if added or removed:
            log.info("Markets updated: +%d -%d, now following %d", len(added), len(removed), len(self.markets))

    async def _send(self, msg: dict) -> None:
        await self.ws.send(json.dumps(msg))

    def handle(self, raw) -> None:
        events = parse_frame(raw)
        if events is None:
            log.warning("Skipping malformed message: %.200s", raw)
            return
        for d in events:
            try:
                self._handle_event(d)
            except (KeyError, TypeError, ValueError, InvalidOperation) as e:
                log.warning("Skipping malformed %s message (%s): %.200s", d.get("event_type"), e, d)

    def _handle_event(self, d: dict) -> None:
        kind = d.get("event_type")
        if kind == "price_change":
            for c in d.get("price_changes") or []:
                book = self.books.get(c.get("asset_id"))
                if book is not None:
                    book.update(c["side"], c["price"], c["size"])
            return

        token = d.get("asset_id")
        where = self.tokens.get(token)
        if kind == "market_resolved":
            market_id = d.get("market")
            if market_id in self.markets:
                asyncio.create_task(self._resolve(market_id))
            return
        if where is None:
            return  # unsubscribed token, new_market, tick_size_change, ...
        market_id, outcome = where

        if kind == "book":
            book = self.books[token]
            book.replace(d.get("bids") or [], d.get("asks") or [])
            if token in self.awaiting_snapshot:
                # First book after (re)subscribing: Kalshi-style catch-up row, marked as a snapshot.
                self.awaiting_snapshot.discard(token)
                bid, ask = book.best()
                self._emit_price(market_id, d["timestamp"], book, bid, ask, snapshot=True)
        elif kind == "best_bid_ask" and outcome == 0:
            self._emit_price(market_id, d["timestamp"], self.books[token], d.get("best_bid"),
                             d.get("best_ask"), snapshot=False)
        elif kind == "last_trade_price":
            row = trade_row(market_id, outcome, d)
            if row is None:
                log.warning("Skipping malformed trade: %.200s", d)
                return
            self.last_price[market_id] = row[4]  # YES price
            self.on_trade(row)

    def _emit_price(self, market_id, ts_ms, book, best_bid, best_ask, snapshot) -> None:
        row = price_row(market_id, ts_ms, book, best_bid, best_ask, self.last_price.get(market_id), snapshot)
        if row is not None:
            self.on_price(row)

    async def _resolve(self, market_id: str) -> None:
        info = self.markets.get(market_id)
        if info is None:
            return
        log.info("Market resolved: %s", market_id)
        await self.sync({m: i for m, i in self.markets.items() if m != market_id})
        await self.close_market(market_id)

