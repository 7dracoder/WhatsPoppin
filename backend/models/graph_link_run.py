from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class GraphLinkRun(Base):
    """The latest link-building run for one company: lets the page poll, and skips rebuilds inside the TTL."""

    __tablename__ = "graph_link_runs"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text)  # 'running' | 'done' | 'error'
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
