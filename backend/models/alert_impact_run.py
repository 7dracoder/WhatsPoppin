from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class AlertImpactRun(Base):
    """Whether an alert has been matched to companies; lets the worker catch up and retry.

    Kept apart from `alerts.status`, which belongs to the future LLM enricher.
    """

    __tablename__ = "alert_impact_runs"

    alert_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[str] = mapped_column(Text)  # 'done' | 'failed'
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    companies: Mapped[int] = mapped_column(Integer, server_default="0")  # impacts stored
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
