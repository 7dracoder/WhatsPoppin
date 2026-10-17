"""Postgres via SQLAlchemy: the company graph's tables, created next to the market tables.

Importing the model modules below registers their tables on the shared Base.metadata, so the
shared connect() (which runs create_all) builds them too, without editing backend/models/__init__.py.
"""

from backend.ingestion.common.db import connect, make_engine  # same engine setup as every other package
from backend.models.base import Base
from backend.models.entity import Entity
from backend.models.entity_relationship import EntityRelationship
from backend.models.graph_board_run import GraphBoardRun
from backend.models.graph_board_seat import GraphBoardSeat
from backend.models.graph_company_profile import GraphCompanyProfile
from backend.models.graph_event import GraphEvent
from backend.models.graph_highlight import GraphHighlight
from backend.models.graph_link_run import GraphLinkRun
from backend.models.graph_processed_filing import GraphProcessedFiling
from backend.models.market_entity import MarketEntity

__all__ = ["connect", "make_engine", "create_tables", "MODELS", "TABLES"]

# Parents before children.
MODELS = (Entity, EntityRelationship, MarketEntity, GraphCompanyProfile, GraphLinkRun,
          GraphProcessedFiling, GraphEvent, GraphHighlight, GraphBoardRun, GraphBoardSeat)
TABLES = [m.__table__ for m in MODELS]


def create_tables(sync_conn) -> None:
    """Create only this feature's tables. Use as `await conn.run_sync(create_tables)`."""
    Base.metadata.create_all(sync_conn, tables=TABLES)
