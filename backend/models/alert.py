from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class Alert(Base):
    """A market move worth a deeper look. Written by alert_detector, claimed by the enricher."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(Text, server_default="pending")  # pending|processing|done|failed
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(Text, server_default="kalshi")
    market_id: Mapped[str] = mapped_column(Text)  # the strongest market in the event
    event_id: Mapped[str | None] = mapped_column(Text)
    series_id: Mapped[str | None] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(Text)  # 'yes_up' | 'yes_down'
    reasons: Mapped[list[str]] = mapped_column(ARRAY(Text))  # price_move, volume_burst, whale, imbalance
    score: Mapped[Decimal] = mapped_column(Numeric)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mid_before: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    mid_now: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    change_pts: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    z_score: Mapped[Decimal | None] = mapped_column(Numeric)
    sigma: Mapped[Decimal | None] = mapped_column(Numeric)
    window_notional: Mapped[Decimal | None] = mapped_column(Numeric)  # $ traded in the window
    volume_ratio: Mapped[Decimal | None] = mapped_column(Numeric)
    imbalance: Mapped[Decimal | None] = mapped_column(Numeric)  # 0..1
    imbalance_side: Mapped[str | None] = mapped_column(Text)  # 'yes' | 'no'
    whale_notional: Mapped[Decimal | None] = mapped_column(Numeric)
    whale_side: Mapped[str | None] = mapped_column(Text)
    is_block_trade: Mapped[bool] = mapped_column(Boolean, server_default="false")
    summary: Mapped[str] = mapped_column(Text)  # one plain-English sentence for the LLM
    context: Mapped[dict] = mapped_column(JSONB)  # everything the LLM needs without extra queries


Index("alerts_pending", Alert.status, Alert.score.desc())
