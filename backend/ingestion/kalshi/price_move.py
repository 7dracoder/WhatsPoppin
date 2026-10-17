"""Price move signal: the 5-minute midpoint change as a z-score.

    z = (midpoint now - midpoint 5 min ago) / typical 5-minute move for this market

A raw change like "+3 cents" means different things in different markets: huge
for a sleepy market, noise for a busy one. Dividing by the market's own typical
5-minute move puts every market on the same scale (|z| of 3 = unusual anywhere).
"""

import asyncio
import json
import statistics
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

REST_URL = "https://external-api.kalshi.com/trade-api/v2"

WINDOW_S = 10 * 60          # live context window kept per market
LOOKBACK_S = 5 * 60         # the "5-minute" in 5-minute change
SETTLE_S = 10 * 60          # ignore this long after open / reconnect / resume
CLOSE_BUFFER_S = 10 * 60    # ignore this long before close
HISTORY_DAYS = 3            # how far back to download 1-minute candles
BASELINE_REFRESH_S = 60 * 60
MIN_SAMPLES = 30            # fewer historical 5-min changes than this = no baseline
MIN_SIGMA = 0.01            # 1 cent floor so near-flat markets don't explode z
MAX_CONCURRENT_FETCHES = 2  # be gentle with Kalshi's REST rate limits (HTTP 429)
REQUEST_PAUSE_S = 0.25


def midpoint(bid, ask) -> float | None:
    """Fair price = halfway between best yes bid and best yes ask.

    We use the midpoint instead of the last trade price because in a quiet market
    the last trade can be minutes or hours old, while quotes update constantly.

    Returns None when there are no real buyers or sellers: Kalshi reports an empty
    bid side as $0 and an empty ask side as $1, and a midpoint built from a
    missing side is a made-up number, not a market price.
    """
    try:
        bid, ask = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if bid <= 0 or ask >= 1 or ask <= bid:
        return None
    return (bid + ask) / 2


def price_at(window, t: float) -> float | None:
    """Most recent price at or before time t.

    A quiet market may have no update at exactly t, but its last price is still
    the price in effect then, so we take the latest one from t or earlier.
    """
    found = None
    for ts, mid in window:
        if ts > t:
            break
        found = mid
    return found


def typical_5min_move(candles, open_ts, close_ts) -> float | None:
    """Standard deviation of past 5-minute midpoint changes, from 1-minute candles."""
    mids = {
        c["end_period_ts"]: midpoint(c.get("yes_bid", {}).get("close_dollars"),
                                     c.get("yes_ask", {}).get("close_dollars"))
        for c in candles
    }
    if not mids:
        return None

    # Kalshi only returns candles for minutes where something happened, so fill
    # the gaps by carrying the last price forward, same rule as price_at().
    grid, last = {}, None
    for t in range(min(mids), max(mids) + 60, 60):
        last = mids.get(t, last)
        grid[t] = last

    changes = []
    for t, mid in grid.items():
        before = grid.get(t - LOOKBACK_S)
        if mid is None or before is None:
            continue  # one side of the book was empty
        # Same exclusions as live: opening and closing jumps aren't "typical".
        if open_ts and t < open_ts + SETTLE_S:
            continue
        if close_ts and t > close_ts - CLOSE_BUFFER_S:
            continue
        changes.append(mid - before)

    if len(changes) < MIN_SAMPLES:
        return None
    return max(statistics.pstdev(changes), MIN_SIGMA)


# --- Kalshi REST (blocking; called via asyncio.to_thread) --------------------
def _get(path: str) -> dict:
    with urllib.request.urlopen(REST_URL + path, timeout=15) as resp:
        data = json.load(resp)
    time.sleep(REQUEST_PAUSE_S)
    return data


def _iso_to_ts(iso: str | None) -> float | None:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else None


def fetch_market(ticker: str) -> dict:
    return _get(f"/markets/{ticker}")["market"]


def fetch_candles(ticker: str, open_ts: float | None) -> list[dict]:
    # Series is the ticker prefix (KXBTCD-26OCT1005-T81399.99 -> KXBTCD).
    series = ticker.split("-")[0]
    end = int(time.time())
    start = int(max(end - HISTORY_DAYS * 86400, open_ts or 0))
    candles = []
    for s in range(start, end, 86400):  # one day per request keeps responses small
        e = min(s + 86400, end)
        candles += _get(f"/series/{series}/markets/{ticker}/candlesticks"
                        f"?start_ts={s}&end_ts={e}&period_interval=1")["candlesticks"]
    return candles


# --- Live tracking ------------------------------------------------------------
@dataclass
class Market:
    ticker: str
    window: deque = field(default_factory=deque)  # (ts, midpoint or None)
    status: str = "loading"
    decided: bool = False
    open_ts: float | None = None
    close_ts: float | None = None
    resumed_at: float = 0.0
    sigma: float | None = None
    baseline_at: float = 0.0


@dataclass
class PriceMove:
    ticker: str
    ts: float
    mid: float | None
    change: float | None = None
    z: float | None = None
    skip: str | None = None


class PriceMoveTracker:
    def __init__(self, series: list[str]):
        self.series = set(series)  # empty = track every market
        self.markets: dict[str, Market] = {}
        self.connected_at = 0.0
        self._fetch_slots = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
        self._loading: set[str] = set()

    def tracks(self, ticker: str) -> bool:
        return not self.series or ticker.split("-")[0] in self.series

    def on_connect(self) -> None:
        # Prices from before a disconnect can't be compared with prices after it:
        # we don't know what happened in between, so start every window fresh.
        self.connected_at = time.time()
        for m in self.markets.values():
            m.window.clear()

    def on_ticker(self, msg: dict) -> PriceMove | None:
        ticker = msg.get("market_ticker")
        if not ticker or not self.tracks(ticker):
            return None
        now = msg.get("ts_ms", time.time() * 1000) / 1000
        m = self.markets.setdefault(ticker, Market(ticker))
        self._maybe_load_baseline(m)

        mid = midpoint(msg.get("yes_bid_dollars"), msg.get("yes_ask_dollars"))
        # Store None too: once the book empties, the old midpoint is no longer valid.
        m.window.append((now, mid))
        # Trim to the window, but keep the newest price older than the cutoff:
        # in a quiet market it is still the price in effect at the window start.
        while len(m.window) >= 2 and m.window[1][0] <= now - WINDOW_S:
            m.window.popleft()

        result = PriceMove(ticker, now, mid)
        result.skip = self._skip_reason(m, now)
        if result.skip:
            return result
        if mid is None:
            result.skip = "no real buyers/sellers"
            return result
        before = price_at(m.window, now - LOOKBACK_S)
        if before is None:
            result.skip = "need 5 min of prices"
            return result
        result.change = mid - before
        result.z = result.change / m.sigma
        return result

    def on_lifecycle(self, msg: dict) -> None:
        m = self.markets.get(msg.get("market_ticker"))
        if m is None:
            return
        kind = msg.get("event_type")
        if kind == "deactivated":  # trading paused
            m.status = "inactive"
        elif kind == "activated":  # opened or resumed: the first prices are catch-up jumps
            m.status = "active"
            m.resumed_at = time.time()
            m.window.clear()
        elif kind == "close_date_updated":
            m.close_ts = msg.get("close_ts", m.close_ts)
        elif kind in ("determined", "settled"):
            m.decided = True

    def _skip_reason(self, m: Market, now: float) -> str | None:
        # Each of these produces price jumps that come from market mechanics,
        # not from news, so a big z-score there would be a false alarm.
        if m.decided:
            return "already decided"
        if m.status != "active":
            return f"status {m.status}"
        if m.open_ts and now < m.open_ts + SETTLE_S:
            return "just opened"
        if now < self.connected_at + SETTLE_S:
            return "just reconnected"
        if now < m.resumed_at + SETTLE_S:
            return "just resumed"
        if m.close_ts and now > m.close_ts - CLOSE_BUFFER_S:
            return "closing soon"
        if m.sigma is None:
            return "no baseline yet"
        return None

    def _maybe_load_baseline(self, m: Market) -> None:
        if m.ticker not in self._loading and time.time() - m.baseline_at > BASELINE_REFRESH_S:
            self._loading.add(m.ticker)
            asyncio.create_task(self._load_baseline(m))

    async def _load_baseline(self, m: Market) -> None:
        try:
            async with self._fetch_slots:
                info = await asyncio.to_thread(fetch_market, m.ticker)
                m.status = info.get("status", "unknown")
                m.decided = bool(info.get("result"))
                m.open_ts = _iso_to_ts(info.get("open_time"))
                m.close_ts = _iso_to_ts(info.get("close_time"))
                candles = await asyncio.to_thread(fetch_candles, m.ticker, m.open_ts)
            m.sigma = typical_5min_move(candles, m.open_ts, m.close_ts)
            m.baseline_at = time.time()
        except Exception as e:  # network/HTTP/JSON errors: log and retry in a minute
            print(f"[baseline] {m.ticker}: {e}")
            m.baseline_at = time.time() - BASELINE_REFRESH_S + 60
        finally:
            self._loading.discard(m.ticker)
