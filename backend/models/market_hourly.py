from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MarketHourly(Base):
    """Hourly summary of live rows, written just before the 30-minute retention deletes them.

    Polymarket US has no public trade history, so its volume and whale baselines are
    built from these summaries as they accumulate. Kept for 14 days.
    """

    __tablename__ = "market_hourly"

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    market_id: Mapped[str] = mapped_column(Text, primary_key=True)
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    quote_rows: Mapped[int] = mapped_column(Integer, server_default="0")  # > 0 = we were watching
    notional: Mapped[Decimal] = mapped_column(Numeric, server_default="0")  # $ traded
    orders: Mapped[int] = mapped_column(Integer, server_default="0")
    order_sizes: Mapped[list[Decimal]] = mapped_column(ARRAY(Numeric), server_default="{}")  # $ per order
