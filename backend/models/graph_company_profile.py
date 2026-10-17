from sqlalchemy import BigInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class GraphCompanyProfile(Base):
    """SEC facts about a company the graph has looked at; backs the same-industry fallback."""

    __tablename__ = "graph_company_profiles"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    cik: Mapped[int] = mapped_column(BigInteger)
    sic_code: Mapped[str | None] = mapped_column(Text)
    sic_description: Mapped[str | None] = mapped_column(Text)
    listing_venue: Mapped[str | None] = mapped_column(Text)  # e.g. 'Nasdaq', 'NYSE'
