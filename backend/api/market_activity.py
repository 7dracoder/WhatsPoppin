"""Has a market actually traded lately? The recommendation list uses this to leave out markets
that are open on their venue but nobody has traded: those have no price history to chart and
nothing to correlate with the stock.

Why ask the venue and not our own tables: `market_prices` / `market_trades` only hold the
markets the streaming workers were told to follow (the top few by volume), so a market absent
there may simply never have been streamed. The venue's own history is the ground truth. Each
check is one or two small HTTP calls, so answers are cached in this process for CACHE_TTL_S.
"""

import asyncio
import logging
import time
from datetime import timedelta

import httpx

from backend.ingestion.kalshi.kalshi import REST_URL, fetch_candles
from backend.ingestion.polymarket import polymarket

log = logging.getLogger(__name__)

LOOKBACK = timedelta(days=7)   # "started" = traded at least once in the last week
CACHE_TTL_S = 30 * 60
CONCURRENCY = 5                # venue calls in flight at once per request

_cache: dict[tuple[str, str], tuple[float, bool]] = {}


async def traded_recently(client: httpx.AsyncClient, source: str, series: str | None, market_id: str,
                          now: float) -> bool:
    """True when the venue has at least one price point for the market in the last LOOKBACK.
    A venue error counts as traded: an outage must not empty the recommendation list."""
    key = (source, market_id)
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < CACHE_TTL_S:
        return hit[1]
    start, end = int(now - LOOKBACK.total_seconds()), int(now)
    try:
        if source == "kalshi":
            candles = await fetch_candles(client, series or market_id.split("-")[0], market_id, start, end, 60)
            traded = any((c.get("price") or {}).get("close_dollars") not in (None, "") for c in candles)
        else:
            token = polymarket.yes_token(await polymarket.fetch_market(client, market_id))
            traded = bool(token) and bool(await polymarket.fetch_price_history(client, token, start, end, 60))
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("activity check for %s %s failed, keeping it: %s", source, market_id, exc)
        return True
    _cache[key] = (now, traded)
    return traded


async def only_traded(markets: list[dict], now: float | None = None) -> list[dict]:
    """The rows of `markets` (db.top_markets dicts) that traded recently, in the same order."""
    if not markets:
        return []
    now = time.time() if now is None else now
    limiter = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(base_url=REST_URL, timeout=15) as client:
        async def check(m: dict) -> bool:
            async with limiter:
                return await traded_recently(client, m["source"], m.get("series_id"), m["market_id"], now)

        keep = await asyncio.gather(*(check(m) for m in markets))
    return [m for m, ok in zip(markets, keep) if ok]
