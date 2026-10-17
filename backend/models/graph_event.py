from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class GraphEvent(Base):
    """A recent news story, X post or prediction-market move about one company. Input to the highlight builder.

    `alert_id` points at `alerts.id` without a foreign key: reset_db drops `alerts` with CASCADE.
    """

    __tablename__ = "graph_events"
    __table_args__ = (UniqueConstraint("entity_symbol", "url"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    entity_symbol: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)  # 'news' | 'market' | 'social'
    event_type: Mapped[str] = mapped_column(Text)  # product_launch|contract|earnings_surprise|recall|acquisition|odds_move
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    alert_id: Mapped[int | None] = mapped_column(BigInteger)


Index("graph_events_recent", GraphEvent.entity_symbol, GraphEvent.occurred_at.desc())
