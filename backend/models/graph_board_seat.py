from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class GraphBoardSeat(Base):
    """A director of one company, from the latest Form 3 or 4 that person filed for it.

    The person is keyed by their own SEC CIK, which is the same in every company's filings: two
    rows with one `person_cik` are the same person on two boards.
    """

    __tablename__ = "graph_board_seats"
    __table_args__ = (UniqueConstraint("company_symbol", "person_cik", name="graph_board_seats_company_person"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    company_symbol: Mapped[str] = mapped_column(Text)
    person_cik: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(Text)  # display name: 'Robyn M Denholm'
    raw_name: Mapped[str] = mapped_column(Text)  # as filed: 'DENHOLM ROBYN M'
    role: Mapped[str] = mapped_column(Text)  # the officer title when the director is also an officer, else 'Director'
    is_officer: Mapped[bool] = mapped_column(Boolean, default=False)
    filed_at: Mapped[date] = mapped_column(Date)  # filing date of the Form 3 or 4 the row comes from
    evidence_url: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
