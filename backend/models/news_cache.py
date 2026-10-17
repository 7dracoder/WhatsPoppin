from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class NewsCache(Base):
    """One NewsAPI answer for (ticker, day-bucketed window), so a metered call is made once per
    window and the demo tickers can be pre-fetched. Windows that ended in the past never change;
    the one that includes today is refreshed after NEWS_REFRESH_S (see evidence/news_cache.py).
    """

    __tablename__ = "news_cache"

    ticker: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    items: Mapped[list] = mapped_column(JSONB)  # ContentItem.model_dump(mode="json") each
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
