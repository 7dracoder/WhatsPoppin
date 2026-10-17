from datetime import datetime

from sqlalchemy import DateTime, Float, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class SentimentCache(Base):
    """Jev's sentiment for a piece of content, so the same text is never paid for twice.

    Keyed by a hash of the content (see classification/sentiment_cache.content_key), not by an
    id: the same wire story shows up under many URLs, and hashing means an edited article
    misses the cache instead of returning a stale label.
    """

    __tablename__ = "sentiment_cache"

    content_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text)  # positive | negative | neutral
    probability: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
