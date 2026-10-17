"""Data models for the alpaca ingestion module.

Pydantic, not plain dataclasses: this module sits at an external boundary
(Alpaca-supplied JSON), same reasoning as ingestion/news_api/models.py.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class ZoomTier(str, Enum):
    RECENT = "recent"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ALL_TIME = "all_time"


class PricePoint(BaseModel):
    """Flat point shape — matches ingestion/polymarket and
    ingestion/kalshi's draft shape field-for-field, minus `question`
    (not applicable to a stock ticker). `price_or_odds` is a bar's close;
    open/high/low are discarded on purpose (see
    new_specs/ingestion/alpaca.md Non-Goals).
    """

    source: str = "alpaca"
    market_id: str  # the ticker
    price_or_odds: float
    volume: int | None = None
    timestamp: datetime


class AlpacaApiError(Exception):
    """Raised for any non-2xx Alpaca response.

    `retryable` is a signal for whatever caller calls this layer later —
    this module never retries on its own.
    """

    def __init__(self, code: str, message: str, retryable: bool):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"[{code}] {message}")
