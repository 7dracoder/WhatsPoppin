"""Polymarket US client: Ed25519 signing, gateway REST discovery, authenticated market stream.

Polymarket US (CFTC-regulated, separate from global Polymarket) has one instrument per
market: buying it = YES, selling it = NO. Its price is the YES price, so no conversion
is needed. Markets are identified by slug.
"""

import asyncio
import base64
import itertools
import json
import logging
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.ingestion.kalshi.kalshi import sign

log = logging.getLogger(__name__)

GATEWAY_URL = "https://gateway.polymarket.us"  # public REST market data, no auth
WS_URL = "wss://api.polymarket.us/v1/ws/markets"
WS_PATH = "/v1/ws/markets"
SOURCE = "polymarket_us"
DISCOVERY_INTERVAL_S = 5 * 60
PAGE = 100
MAX_PAGES_PER_TAG = 5
SUBSCRIBE_CHUNK = 100  # documented maximum markets per subscription
ZERO, ONE = Decimal(0), Decimal(1)


# --- Signing --------------------------------------------------------------------------
def load_secret(secret_b64: str) -> Ed25519PrivateKey:
    """The secret key from polymarket.us/developer is base64; its first 32 bytes are the Ed25519 seed."""
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(secret_b64)[:32])


def auth_headers(key_id: str, private_key) -> dict:
    timestamp = str(int(time.time() * 1000))
    return {
        "X-PM-Access-Key": key_id,
        "X-PM-Timestamp": timestamp,
        "X-PM-Signature": sign(private_key, timestamp + "GET" + WS_PATH),
    }


# --- Discovery ----------------------------------------------------------------------------
_FRACTION = re.compile(r"(\.\d{6})\d+")


def parse_time(iso: str | None) -> datetime | None:
    """ISO 8601 with up to nanoseconds ('...:08.414797335Z') -> datetime."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(_FRACTION.sub(r"\1", iso).replace("Z", "+00:00"))
    except ValueError:
        return None


def market_rows(events: list[dict], tags: list[str], max_markets: int, now: float) -> list[dict]:
    """Open markets of these events -> `markets` rows, in the order given (events come most-traded first)."""
    rows, seen = [], set()
    for ev in events:
        event_tags = {t.get("slug"): t.get("label") for t in ev.get("tags") or []}
        category = next((event_tags[t] for t in tags if t in event_tags), None) or ev.get("category")
        for m in ev.get("markets") or []:
            slug = m.get("slug")
            close = parse_time(m.get("endDate"))
            if (not slug or slug in seen or not m.get("active") or m.get("closed")
                    or close is None or close.timestamp() <= now):
                continue
            seen.add(slug)
            rows.append({
                "market_id": slug,
                "title": m.get("question") or ev.get("title"),
                "outcome_label": m.get("title") or None,
                "rules_primary": m.get("description"),
                "event_id": str(ev.get("id")),
                "event_title": ev.get("title"),
                "series_id": ev.get("seriesSlug"),
                "series_title": None,
                "category": category,
                "tags": [label for label in event_tags.values() if label],
                "close_time": close,
                "url": f"https://polymarket.us/event/{ev.get('slug')}",
            })
    return rows[:max_markets]


async def fetch_events(client: httpx.AsyncClient, tag: str) -> list[dict]:
    events = []
    for page in range(MAX_PAGES_PER_TAG):
        resp = await client.get(f"/v2/tags/{tag}/events", params={"limit": PAGE, "offset": page * PAGE})
        resp.raise_for_status()
        batch = resp.json().get("events") or []
        events += batch
        if len(batch) < PAGE:
            break
    return events


# --- Messages -> rows -------------------------------------------------------------------
def _px(obj) -> Decimal | None:
    """{"value": "0.5920", "currency": "USD"} -> Decimal; missing/empty -> None."""
    try:
        return Decimal(obj["value"]) if obj else None
    except (KeyError, TypeError, InvalidOperation):
        return None


def _num(value) -> Decimal | None:
    try:
        return None if value in (None, "") else Decimal(value)
    except (TypeError, InvalidOperation):
        return None


def top_of_book(md: dict) -> tuple:
    """(bid, bid_size, ask, ask_size) from a marketData message; an empty side -> (None, 0)."""
    bids = [(_px(l.get("px")), _num(l.get("qty"))) for l in md.get("bids") or []]
    asks = [(_px(l.get("px")), _num(l.get("qty"))) for l in md.get("offers") or []]
    bid = max((b for b in bids if b[0] is not None), default=(None, ZERO))
    ask = min((a for a in asks if a[0] is not None), default=(None, ZERO))
    return bid[0], bid[1] or ZERO, ask[0], ask[1] or ZERO


def price_row(md: dict, snapshot: bool) -> tuple | None:
    """marketData -> market_prices row (see ingestion.common.db.PRICE_COLUMNS), or None.

    An empty side is stored like Kalshi does (bid 0 / ask 1, size 0), which the
    detector's good-quote rule rejects.
    """
    ts = parse_time(md.get("transactTime"))
    if ts is None or not md.get("marketSlug"):
        return None
    bid, bid_size, ask, ask_size = top_of_book(md)
    stats = md.get("stats") or {}
    last = _px(stats.get("lastTradePx"))
    if last is None:
        last = (bid + ask) / 2 if bid is not None and ask is not None else (bid or ask)
    if last is None:
        return None
    return (SOURCE, md["marketSlug"], ts, last, bid if bid is not None else ZERO,
            ask if ask is not None else ONE, bid_size, ask_size,
            _num(stats.get("sharesTraded")), _num(stats.get("openInterest")), snapshot)


def trade_row(t: dict) -> tuple | None:
    """trade -> market_trades row (see ingestion.common.db.TRADE_COLUMNS).

    Verified live: a taker BUY trades at the ask and buys YES (intent BUY_LONG or
    SELL_SHORT); a taker SELL trades at the bid and is a NO buyer (SELL_LONG / BUY_SHORT).
    """
    try:
        side = t["taker"]["side"]
        if side not in ("ORDER_SIDE_BUY", "ORDER_SIDE_SELL"):
            return None
        price, qty, ts = _px(t["price"]), _px(t["quantity"]), parse_time(t["tradeTime"])
        if price is None or qty is None or ts is None:
            return None
        return (SOURCE, t["marketSlug"], ts, t.get("id") or f"{t['marketSlug']}:{t['tradeTime']}",
                price, qty, "yes" if side == "ORDER_SIDE_BUY" else "no", False)
    except (KeyError, TypeError):
        return None


# --- WebSocket session --------------------------------------------------------------------
class MarketSession:
    """One authenticated WebSocket connection: subscribe in chunks, keep the list in sync, emit rows.

    `discover` is an async callable returning the set of market slugs to follow.
    """

    def __init__(self, key_id, private_key, discover, on_price, on_trade):
        self.key_id = key_id
        self.private_key = private_key
        self.discover = discover
        self.on_price = on_price
        self.on_trade = on_trade
        self.ws = None
        self.followed: set[str] = set()
        self.request_ids: list[str] = []
        self.awaiting_snapshot: set[str] = set()
        self.last_top: dict[str, tuple] = {}  # slug -> last written (bid, bid_size, ask, ask_size)
        self._ids = itertools.count(1)

    async def run(self) -> None:
        """Returns or raises when the connection ends; the caller reconnects."""
        followed = await self.discover()
        headers = auth_headers(self.key_id, self.private_key)
        async with websockets.connect(WS_URL, additional_headers=headers, max_size=None) as ws:
            self.ws = ws
            log.info("WebSocket connected")
            await self.sync(followed)
            refresher = asyncio.create_task(self._refresh_loop())
            try:
                async for raw in ws:
                    self.handle(raw)
            finally:
                refresher.cancel()

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(DISCOVERY_INTERVAL_S)
            try:
                await self.sync(await self.discover())
            except (httpx.HTTPError, ValueError, KeyError) as e:
                log.warning("Discovery failed, keeping current markets: %s", e)

    async def sync(self, followed: set[str]) -> None:
        """Subscriptions are per request id, so on any change unsubscribe them all and resubscribe.

        Only newly added markets get their first book marked as a snapshot; for the
        others the resubscribe book is simply the current state.
        """
        added, removed = followed - self.followed, self.followed - followed
        if not added and not removed:
            return
        for request_id in self.request_ids:
            await self._send({"unsubscribe": {"requestId": request_id}})
        self.request_ids = []
        self.awaiting_snapshot |= added
        self.awaiting_snapshot -= removed
        for slug in removed:
            self.last_top.pop(slug, None)
        self.followed = set(followed)
        slugs = sorted(followed)
        for i in range(0, len(slugs), SUBSCRIBE_CHUNK):
            for kind in ("SUBSCRIPTION_TYPE_MARKET_DATA", "SUBSCRIPTION_TYPE_TRADE"):
                request_id = f"{kind.rsplit('_', 1)[-1].lower()}-{next(self._ids)}"
                self.request_ids.append(request_id)
                await self._send({"subscribe": {"requestId": request_id, "subscriptionType": kind,
                                                "marketSlugs": slugs[i:i + SUBSCRIBE_CHUNK]}})
        log.info("Markets updated: +%d -%d, now following %d", len(added), len(removed), len(followed))

    async def _send(self, msg: dict) -> None:
        await self.ws.send(json.dumps(msg))

    def handle(self, raw) -> None:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            log.warning("Skipping malformed message: %.200s", raw)
            return
        if not isinstance(data, dict):
            return
        if "marketData" in data:
            self._on_market_data(data["marketData"])
        elif "trade" in data:
            t = data["trade"]
            if isinstance(t, dict) and t.get("marketSlug") in self.followed:
                row = trade_row(t)
                if row is None:
                    log.warning("Skipping malformed trade: %.200s", raw)
                else:
                    self.on_trade(row)
        elif "error" in data:
            log.error("Polymarket US error: %s", data)
        # heartbeat and subscription acknowledgements need no action

    def _on_market_data(self, md) -> None:
        if not isinstance(md, dict) or md.get("marketSlug") not in self.followed:
            return
        slug = md["marketSlug"]
        snapshot = slug in self.awaiting_snapshot
        top = top_of_book(md)
        # Full-book messages repeat for changes deep in the book; like Kalshi's ticker,
        # only write a row when the top of the book (prices or sizes) changes.
        if not snapshot and self.last_top.get(slug) == top:
            return
        row = price_row(md, snapshot)
        if row is None:
            return
        self.awaiting_snapshot.discard(slug)
        self.last_top[slug] = top
        self.on_price(row)
