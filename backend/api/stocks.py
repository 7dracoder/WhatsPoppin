"""`/stocks` router: on-demand stock price lookups via ingestion/alpaca."""

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.deps import get_alpaca_gateway
from backend.api.trading_days import parse_end, with_fallback
from backend.ingestion.alpaca import (
    AlpacaApiError,
    AlpacaApiGateway,
    PricePoint,
    ZoomTier,
    fetch_price_series,
)

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/{ticker}/prices", response_model=list[PricePoint])
def get_prices(
    ticker: str,
    tier: ZoomTier = ZoomTier.DAILY,
    end: str | None = Query(None, description="ISO 8601 window end; defaults to now. Weekends snap to Friday's close."),
    gateway: AlpacaApiGateway = Depends(get_alpaca_gateway),
) -> list[PricePoint]:
    """Bars for the tier's window ending at `end`. The intraday tiers (recent, daily) step back to
    the last session with bars, so a weekend or holiday shows the previous trading day, not nothing.
    """
    try:
        if tier in (ZoomTier.RECENT, ZoomTier.DAILY):
            bars, _ = with_fallback(parse_end(end), lambda at: fetch_price_series(ticker.upper(), tier, gateway, now=at),
                                    lambda bars: not bars)
            return bars
        return fetch_price_series(ticker.upper(), tier, gateway, now=parse_end(end))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Bad end: {exc}") from exc
    except AlpacaApiError as exc:
        raise HTTPException(status_code=502 if exc.retryable else 400, detail=exc.message) from exc
