from datetime import datetime

from sqlalchemy import DateTime, Float, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MarketEmbedding(Base):
    """One embedding per market event (backend/llm/embeddings.py), for semantic market search and the
    enrichment shortlist. Written by the enrichment worker and, for events it hasn't reached yet, by
    GET /markets/search on demand.

    Keyed like enrichment: an event is its `event_id`, or the market itself when it has none. `text_hash`
    is the hash of the text that was embedded, so an edited market is re-embedded. No foreign key to
    `markets`: reset_db drops that table with CASCADE.
    """

    __tablename__ = "market_embeddings"

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    event_key: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    text_hash: Mapped[str] = mapped_column(Text)
    vector: Mapped[list[float]] = mapped_column(ARRAY(Float(precision=24)))  # REAL[]; normalized
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
