from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MarketBaseline(Base):
    """What "normal" looks like for one market, computed from days of history by `python -m baselines`.

    The alert detector compares the live 5-minute window against these numbers.
    """

    __tablename__ = "market_baselines"

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    market_id: Mapped[str] = mapped_column(Text, primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    method: Mapped[str] = mapped_column(Text)  # where the history came from, e.g. 'kalshi_rest'
    # Price: standard deviation of 5-minute midpoint changes (0-1 scale), and how many changes it used.
    sigma_5m: Mapped[Decimal | None] = mapped_column(Numeric)
    sigma_samples: Mapped[int] = mapped_column(Integer, server_default="0")
    # Volume: average $ traded per 5-minute window, over `history_minutes` of history.
    volume_per_window: Mapped[Decimal | None] = mapped_column(Numeric)
    history_minutes: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    # Whale: 99th-percentile order size in $, and how many orders it used.
    whale_p99: Mapped[Decimal | None] = mapped_column(Numeric)
    whale_orders: Mapped[int] = mapped_column(Integer, server_default="0")
