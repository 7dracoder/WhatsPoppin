from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class Entity(Base):
    """A tracked company, person or industry (mock_db_design/db-design.md, table 1)."""

    __tablename__ = "entities"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)  # 'NVDA'; the name itself when there is no US ticker
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)  # 'company' | 'person' | 'industry'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
