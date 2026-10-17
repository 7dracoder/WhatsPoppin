"""Fetch each source's history over REST and turn it into a compute.History.

Verified live (2026-10-10):
- Kalshi: 1-minute candlesticks (sparse: only minutes with activity) and /markets/trades.
- Polymarket: clob /prices-history with startTs/endTs and fidelity=5 gives 5-minute points
  for days; data-api /trades gives taker trades with outcomeIndex and side; Gamma
  /markets?condition_ids=...&closed=false (one parameter per id) gives the token ids.
- Polymarket US: gateway /v1/price-history (5-minute bid/ask display prices for the last
  day). There is no public trade history, so volume and whale numbers come from our own
  market_hourly rollups instead (see compute.from_hourly).
"""

import asyncio
import json
import logging
from datetime import datetime

import httpx

from alert_detector.signals import Trade, midpoint, notional
from baselines.compute import History

log = logging.getLogger(__name__)

KALSHI_URL = "https://external-api.kalshi.com/trade-api/v2"
GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
DATA_URL = "https://data-api.polymarket.com"
US_URL = "https://gateway.polymarket.us"

MAX_TRADE_PAGES = 10  # busy markets: stop after this many pages and count from the oldest trade fetched
REQUEST_PAUSE_S = 0.1
DAY = 86400


async def get_json(client: httpx.AsyncClient, url: str, params=None):
    """GET with a short pause, and backoff on 429 / 5xx (rate limits are per IP)."""
    for attempt in range(4):
        resp = await client.get(url, params=params)
        await asyncio.sleep(REQUEST_PAUSE_S)
        if resp.status_code == 429 or resp.status_code >= 500:
            await asyncio.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()


def _f(value) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


# --- Kalshi -------------------------------------------------------------------------------
def kalshi_points(candles: list[dict], max_spread: float) -> list:
    """Candles -> (t, mid or None). Missing minutes mean nothing changed, so the fetcher
    uses a long lookback; a candle without a two-sided book gives None (breaks the series)."""
    points = []
    for c in candles:
        bid = _f((c.get("yes_bid") or {}).get("close_dollars"))
        ask = _f((c.get("yes_ask") or {}).get("close_dollars"))
        good = bid is not None and ask is not None and 0 < bid and ask < 1 and ask - bid <= max_spread
        points.append((float(c["end_period_ts"]), midpoint(bid, ask) if good else None))
    return sorted(points, key=lambda p: p[0])


def kalshi_trade(t: dict) -> Trade | None:
    try:
        side = t.get("taker_outcome_side") or t["taker_side"]
        yes_price, count = float(t["yes_price_dollars"]), float(t["count_fp"])
        ts = datetime.fromisoformat(t["created_time"].replace("Z", "+00:00")).timestamp()
    except (KeyError, TypeError, ValueError):
        return None
    if side not in ("yes", "no"):
        return None
    return Trade(ts, notional(count, yes_price, side), side, yes_price, bool(t.get("is_block_trade")))


async def kalshi_history(client: httpx.AsyncClient, market: dict, since: float, now: float, cfg) -> History:
    ticker, series = market["market_id"], market["series_id"] or market["market_id"].split("-")[0]
    candles = []
    for start in range(int(since), int(now), DAY):  # one day per request keeps responses small
        data = await get_json(client, f"{KALSHI_URL}/series/{series}/markets/{ticker}/candlesticks",
                              {"start_ts": start, "end_ts": min(start + DAY, int(now)), "period_interval": 1})
        candles += data.get("candlesticks") or []
    trades, cursor, trades_from = [], "", since
    for page in range(MAX_TRADE_PAGES):
        data = await get_json(client, f"{KALSHI_URL}/markets/trades",
                              {"ticker": ticker, "min_ts": int(since), "limit": 1000, "cursor": cursor})
        trades += [t for t in map(kalshi_trade, data.get("trades") or []) if t]
        cursor = data.get("cursor") or ""
        if not cursor:
            break
    else:
        trades_from = min(t.t for t in trades)  # capped: only count the period we actually have
    return History(kalshi_points(candles, cfg.max_spread), 60, 6 * 3600, trades, trades_from, "kalshi_rest")


# --- Polymarket (global) ----------------------------------------------------------------
async def polymarket_tokens(client: httpx.AsyncClient, condition_ids: list[str]) -> dict[str, str]:
    """conditionId -> outcome-0 token id (our YES), 50 markets per Gamma request."""
    tokens = {}
    for i in range(0, len(condition_ids), 50):
        params = [("condition_ids", c) for c in condition_ids[i:i + 50]] + [("closed", "false"), ("limit", 100)]
        for m in await get_json(client, f"{GAMMA_URL}/markets", params):
            ids = m.get("clobTokenIds")
            ids = json.loads(ids) if isinstance(ids, str) else ids
            if ids:
                tokens[m["conditionId"]] = str(ids[0])
    return tokens


def polymarket_trade(t: dict) -> Trade | None:
    """data-api trade -> YES point of view (same table as the live worker)."""
    try:
        price, size, side, outcome = float(t["price"]), float(t["size"]), t["side"], int(t["outcomeIndex"])
        ts = float(t["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None
    if side not in ("BUY", "SELL") or outcome not in (0, 1):
        return None
    bought = side == "BUY"
    if outcome == 0:
        yes_price, taker = price, ("yes" if bought else "no")
    else:
        yes_price, taker = 1 - price, ("no" if bought else "yes")
    return Trade(ts, notional(size, yes_price, taker), taker, yes_price, False)


async def polymarket_history(client: httpx.AsyncClient, market: dict, token: str, since: float, now: float,
                             cfg) -> History:
    data = await get_json(client, f"{CLOB_URL}/prices-history",
                          {"market": token, "startTs": int(since), "endTs": int(now), "fidelity": 5})
    # "p" is Polymarket's display price for outcome 0; there is no bid/ask in the history.
    points = sorted((float(h["t"]), float(h["p"])) for h in data.get("history") or [])
    trades, trades_from = [], since
    for page in range(MAX_TRADE_PAGES):
        batch = await get_json(client, f"{DATA_URL}/trades",
                               {"market": market["market_id"], "limit": 500, "offset": page * 500})
        parsed = [t for t in map(polymarket_trade, batch or []) if t]
        trades += [t for t in parsed if t.t >= since]
        if len(batch or []) < 500 or (parsed and min(t.t for t in parsed) < since):
            break  # newest first: reached the start of the window
    else:
        trades_from = max(since, min(t.t for t in trades))
    return History(points, 300, 600, trades, trades_from, "polymarket_rest")


# --- Polymarket US ------------------------------------------------------------------------
def us_points(history: list[dict], max_spread: float) -> list:
    """longPrice ~ best ask, shortPrice ~ 1 - best bid (per the docs) -> mid, or None if too wide."""
    points = []
    for h in history:
        ask, short = _f(h.get("longPrice")), _f(h.get("shortPrice"))
        bid = None if short is None else 1 - short
        good = bid is not None and ask is not None and 0 < bid <= ask < 1 and ask - bid <= max_spread
        points.append((float(h["timestamp"]), midpoint(bid, ask) if good else None))
    return sorted(points, key=lambda p: p[0])


async def polymarket_us_history(client: httpx.AsyncClient, market: dict, cfg) -> History:
    data = await get_json(client, f"{US_URL}/v1/price-history",
                          {"symbol": market["market_id"], "fixedInterval": "INTERVAL_1D", "fidelity": 5})
    points = us_points(data.get("history") or [], cfg.max_spread)
    return History(points, 300, 600, [], 0.0, "polymarket_us_rest+hourly")
