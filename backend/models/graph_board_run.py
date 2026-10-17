from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class GraphBoardRun(Base):
    """The latest board-building run for one company. Same columns as GraphLinkRun, kept apart so
    a board run never changes what a link run reports."""

    __tablename__ = "graph_board_runs"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text)  # 'running' | 'done' | 'error'
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
