from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Index, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class AlertImpact(Base):
    """A company an alert's market may affect (new_specs/alert-company-impact.md).

    `alert_id` points at `alerts.id` without a foreign key: reset_db drops `alerts` with CASCADE.
    """

    __tablename__ = "alert_impacts"

    alert_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_symbol: Mapped[str] = mapped_column(Text, primary_key=True)  # the affected company
    tier: Mapped[int] = mapped_column(Integer)  # 1 = tagged on the market, 2 = linked to a tier-1 company, 3 = Jev
    matched: Mapped[list[str]] = mapped_column(ARRAY(Text))  # why: the tag, the tier-1 companies, or the market's tags
    relevance: Mapped[Decimal | None] = mapped_column(Numeric)  # tier 3: Jev's probability
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("alert_impacts_company", AlertImpact.entity_symbol, AlertImpact.alert_id.desc())
