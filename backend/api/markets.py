"""`/markets` router: probability history for a Kalshi market, shaped like
the Alpaca PricePoint so the frontend can chart both with one component.
"""

import hashlib
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.deps import get_kalshi_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/markets", tags=["markets"])

RangeKey = Literal["1w", "1m", "3m"]

# range -> (lookback, Kalshi candle period in minutes). Kalshi only accepts
# 1, 60 or 1440. Hourly for 1m keeps ~720 points; daily for 3m keeps ~90.
RANGE_CONFIG: dict[str, tuple[timedelta, int]] = {
    "1w": (timedelta(days=7), 60),
    "1m": (timedelta(days=30), 60),
    "3m": (timedelta(days=90), 1440),
}


class MarketPricePoint(BaseModel):
    source: str = "kalshi"
    market_id: str
    price_or_odds: float  # implied probability, 0-100
    timestamp: datetime  # UTC


def _dollars(value) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def candles_to_points(market_id: str, candles: list[dict]) -> list[MarketPricePoint]:
    """Kalshi candles -> points. Close price is null in periods with no trades,
    so fall back to the bid/ask midpoint; skip the candle if neither exists.
    """
    points = []
    for c in candles:
        price = _dollars((c.get("price") or {}).get("close_dollars"))
        if price is None:
            bid = _dollars((c.get("yes_bid") or {}).get("close_dollars"))
            ask = _dollars((c.get("yes_ask") or {}).get("close_dollars"))
            if bid is None or ask is None:
                continue
            price = (bid + ask) / 2
        points.append(MarketPricePoint(
            market_id=market_id,
            price_or_odds=round(price * 100, 2),
            timestamp=datetime.fromtimestamp(c["end_period_ts"], tz=timezone.utc),
        ))
    return sorted(points, key=lambda p: p.timestamp)


def fetch_kalshi_series(
    market_id: str, range_key: RangeKey, client: httpx.Client, now: datetime | None = None
) -> list[MarketPricePoint]:
    """Real data. Raises httpx.HTTPError / ValueError / KeyError on failure; the route falls back."""
    now = now or datetime.now(timezone.utc)
    lookback, period = RANGE_CONFIG[range_key]
    series = market_id.split("-")[0]  # series ticker is the market ticker's prefix
    resp = client.get(
        f"/series/{series}/markets/{market_id}/candlesticks",
        params={
            "start_ts": int((now - lookback).timestamp()),
            "end_ts": int(now.timestamp()),
            "period_interval": period,
        },
    )
    resp.raise_for_status()
    return candles_to_points(market_id, resp.json().get("candlesticks") or [])


# --- FALLBACK ONLY: fake data so the frontend works without Kalshi access. ---
def mock_series(market_id: str, range_key: RangeKey, now: datetime | None = None) -> list[MarketPricePoint]:
    """Deterministic random walk seeded by market_id (same id -> same shape).
    Not real data. Timestamps are jittered to look like sparse trading.
    """
    now = now or datetime.now(timezone.utc)
    lookback, period = RANGE_CONFIG[range_key]
    # hashlib, not hash(): str hashes are salted per process and would break determinism.
    rng = random.Random(int(hashlib.sha256(market_id.encode()).hexdigest(), 16))
    step = timedelta(minutes=period)
    t, price, points = now - lookback, rng.uniform(20, 80), []
    while t <= now:
        price = min(99.0, max(1.0, price + rng.gauss(0, 2.5)))
        points.append(MarketPricePoint(market_id=market_id, price_or_odds=round(price, 2), timestamp=t))
        t += step * rng.uniform(0.5, 1.5)
    return points


@router.get("/{market_id}/series", response_model=list[MarketPricePoint])
def get_market_series(
    market_id: str,
    range: RangeKey = "1m",  # noqa: A002 - query param name is part of the API
    mock: bool = True,
    client: httpx.Client = Depends(get_kalshi_client),
) -> list[MarketPricePoint]:
    """Kalshi's candles for the market. With `mock=false` a market Kalshi has nothing for is a 404
    rather than the made-up series: a chart that claims to show a market must not draw a fake line.
    """
    try:
        points = fetch_kalshi_series(market_id, range, client)
        if points:
            return points
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("Kalshi candlesticks failed for %s: %s", market_id, exc)
    if not mock:
        raise HTTPException(404, f"No Kalshi price history for {market_id}")
    return mock_series(market_id, range)
