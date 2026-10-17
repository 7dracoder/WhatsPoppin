"""Entry point for the alpaca ingestion module. The rest of the codebase
(a future FastAPI route backing display-charting) should only import
fetch_price_series — everything else in this package is an implementation
detail reachable through it.
"""

from datetime import datetime, timezone

from .client import AlpacaApiGateway
from .models import PricePoint, ZoomTier
from .normalize import normalize_batch
from .tiers import tier_range, tier_timeframe

__all__ = ["fetch_price_series"]


def fetch_price_series(
    ticker: str,
    tier: ZoomTier,
    gateway: AlpacaApiGateway,
    now: datetime | None = None,
) -> list[PricePoint]:
    """On-demand price series for one ticker at one zoom tier.

    `now` is injectable for tests; defaults to the current UTC time.

    Does not: retry on AlpacaApiError, cache across calls, or decide what
    counts as a usable "real-time" view for the Recent tier — the caller
    sees the free tier's delay directly, from the last point's timestamp,
    since `tier_range` never shrinks the request window to hide it. All
    out of scope for this layer (see new_specs/ingestion/alpaca.md
    Non-Goals).
    """
    now = now or datetime.now(timezone.utc)
    start, end = tier_range(tier, now)
    raw_bars = gateway.fetch_bars(ticker, tier_timeframe(tier), start, end)
    return normalize_batch(ticker, raw_bars)
