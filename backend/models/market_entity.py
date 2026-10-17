from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MarketEntity(Base):
    """A prediction market linked to a company it names (db-design.md, table 7).

    No foreign key to `markets`: python -m backend.ingestion.reset_db drops that table with CASCADE.
    """

    __tablename__ = "market_entities"
    # Entity -> markets lookups (search); the primary key only serves market -> entities.
    __table_args__ = (Index("market_entities_entity", "entity_symbol", "source", "market_id"),)

    source: Mapped[str] = mapped_column(Text, primary_key=True)  # 'kalshi' | 'polymarket' | ...
    market_id: Mapped[str] = mapped_column(Text, primary_key=True)
    entity_symbol: Mapped[str] = mapped_column(Text, ForeignKey("entities.symbol"), primary_key=True)
