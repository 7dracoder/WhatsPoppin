from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class GraphProcessedFiling(Base):
    """A filing the link finder already read, so it is never read (or sent to the model) twice."""

    __tablename__ = "graph_processed_filings"

    accession_number: Mapped[str] = mapped_column(Text, primary_key=True)  # '0001318605-25-000012'
    cik: Mapped[int] = mapped_column(BigInteger)
    form: Mapped[str] = mapped_column(Text)  # '10-K' | '8-K' | ...
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
