from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class GraphHighlight(Base):
    """A linked company a recent event may affect. Called a highlight, not a signal (alert_detector uses that)."""

    __tablename__ = "graph_highlights"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("graph_events.id", ondelete="CASCADE"))
    source_symbol: Mapped[str] = mapped_column(Text)  # the company the event is about
    target_symbol: Mapped[str] = mapped_column(Text)  # the linked company it may affect
    direction: Mapped[str] = mapped_column(Text)  # 'may_benefit' | 'may_face_pressure'
    reason: Mapped[str] = mapped_column(Text)  # one factual sentence; never a price prediction
    source_url: Mapped[str] = mapped_column(Text)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price_change_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("graph_highlights_target", GraphHighlight.target_symbol, GraphHighlight.event_time.desc())
