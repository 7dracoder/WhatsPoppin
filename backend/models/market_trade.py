from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKeyConstraint, Index, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MarketTrade(Base):
    """One row per executed trade, from the YES point of view. Kept for 30 minutes, then rolled up into market_hourly."""

    __tablename__ = "market_trades"
    __table_args__ = (
        ForeignKeyConstraint(["source", "market_id"], ["markets.source", "markets.market_id"]),
        Index("market_trades_market_time", "source", "market_id", "timestamp"),
        Index("market_trades_time", "timestamp"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text, server_default="kalshi")
    market_id: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # Kalshi ts_ms
    trade_id: Mapped[str] = mapped_column(Text)
    yes_price: Mapped[Decimal] = mapped_column(Numeric(6, 4))  # YES price the trade executed at
    count: Mapped[Decimal] = mapped_column(Numeric(20, 2))  # contracts
    taker_side: Mapped[str] = mapped_column(Text)  # 'yes' | 'no': the outcome the taker bought
    is_block_trade: Mapped[bool] = mapped_column(Boolean, server_default="false")
