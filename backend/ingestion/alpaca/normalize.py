"""Maps raw Alpaca bar dicts to the shared flat PricePoint shape.

Keeps only the close price (`c`) and volume (`v`); open/high/low are
discarded on purpose, to match the flat shape ingestion/polymarket and
ingestion/kalshi already use (see new_specs/ingestion/alpaca.md
Non-Goals). Stays a pure 1:1 mapping otherwise, same convention as
ingestion/news_api/normalize.py.
"""

import logging

from .models import PricePoint

logger = logging.getLogger(__name__)


def normalize_bar(ticker: str, raw: dict) -> PricePoint:
    """Map one raw Alpaca bar dict to a PricePoint.

    close <- raw["c"]
    volume <- raw["v"]
    timestamp <- raw["t"]

    Raises ValueError if close or timestamp is missing — both required.
    """
    close = raw.get("c")
    timestamp = raw.get("t")
    if close is None:
        raise ValueError("bar missing required field: c (close)")
    if not timestamp:
        raise ValueError("bar missing required field: t (timestamp)")

    return PricePoint(
        market_id=ticker,
        price_or_odds=close,
        volume=raw.get("v"),
        timestamp=timestamp,
    )


def normalize_batch(ticker: str, raw_bars: list[dict]) -> list[PricePoint]:
    """normalize_bar over a list; skips (logs, doesn't raise on) any bar
    that fails normalize_bar's validation, so one malformed bar doesn't
    fail the whole series.
    """
    points: list[PricePoint] = []
    for raw in raw_bars:
        try:
            points.append(normalize_bar(ticker, raw))
        except (ValueError, KeyError) as exc:
            logger.warning("skipping malformed bar: %s", exc)
    return points
