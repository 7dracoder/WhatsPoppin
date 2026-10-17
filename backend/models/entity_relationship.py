import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, JSON, Numeric, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class EntityRelationship(Base):
    """A company-graph edge (db-design.md, table 4), plus `summary` and `evidence_url`.

    `relationship_type` is the related company's role: 'supplier' means it supplies the entity.
    """

    __tablename__ = "entity_relationships"
    __table_args__ = (UniqueConstraint("entity_symbol", "related_entity_symbol", "relationship_type"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    entity_symbol: Mapped[str] = mapped_column(Text, ForeignKey("entities.symbol"))
    related_entity_symbol: Mapped[str] = mapped_column(Text, ForeignKey("entities.symbol"))
    relationship_type: Mapped[str] = mapped_column(Text)  # supplier|customer|partner|competitor|sector_peer
    weight: Mapped[Decimal] = mapped_column(Numeric, server_default="1")
    confidence: Mapped[Decimal] = mapped_column(Numeric)  # 0.9 for 'filing', 0.3 for 'sector' (placeholders)
    source: Mapped[str] = mapped_column(Text)  # 'filing' | 'sector'
    last_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    summary: Mapped[str | None] = mapped_column(Text)  # one factual sentence: what the filing says
    evidence_url: Mapped[str | None] = mapped_column(Text)  # the filing fetched in the run that saved this edge
    # F14: short keywords for Kalshi-market matching. Postgres: native TEXT[] (matches the
    # ALTER TABLE already run against Neon). SQLite (tests only): JSON, since SQLite has no
    # array type — ARRAY doesn't compile there and this table IS created on SQLite in tests
    # (unlike markets/alerts, whose ARRAY columns are only ever DDL-compiled against Postgres).
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text).with_variant(JSON(none_as_null=True), "sqlite"))
