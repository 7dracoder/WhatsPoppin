from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKeyConstraint, Index, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MarketPrice(Base):
    """One row per quote update (Kalshi ticker, Polymarket best bid/ask). Kept for 30 minutes, then rolled up into market_hourly."""

    __tablename__ = "market_prices"
    __table_args__ = (
        ForeignKeyConstraint(["source", "market_id"], ["markets.source", "markets.market_id"]),
        Index("market_prices_market_time", "source", "market_id", "timestamp"),
        Index("market_prices_time", "timestamp"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text, server_default="kalshi")
    market_id: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # Kalshi ts_ms
    price_or_odds: Mapped[Decimal] = mapped_column(Numeric(6, 4))  # last traded YES price
    yes_bid: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    yes_ask: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    yes_bid_size: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    yes_ask_size: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    snapshot: Mapped[bool] = mapped_column(Boolean, server_default="false")
