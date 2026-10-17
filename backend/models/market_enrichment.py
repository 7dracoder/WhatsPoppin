from datetime import datetime

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MarketEnrichment(Base):
    """Jev entity enrichment status per market. Written by the enrichment worker (backend/enrichment).

    No foreign key to `markets`: python -m backend.ingestion.reset_db drops that table with CASCADE.
    A market with no row here has never been picked up.
    """

    __tablename__ = "market_enrichment"

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    market_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text)  # 'pending' | 'done' | 'failed'
    map_version: Mapped[int | None] = mapped_column(Integer)  # entity_map.json version of the last attempt
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")  # failures in a row under map_version
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
