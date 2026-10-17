"""The API shape (GET /graph/{ticker}, GET /graph/{ticker}/board, GET /companies/search), the
allowed values, and the fixtures.

frontend/src/types/graph.ts mirrors these models; keep the two in step.
"""

import json
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

RelationshipType = Literal["supplier", "customer", "partner", "competitor", "sector_peer"]
RelationshipSource = Literal["filing", "sector"]
EventType = Literal["product_launch", "contract", "earnings_surprise", "recall", "acquisition", "odds_move"]
EventSource = Literal["news", "market", "social"]
Direction = Literal["may_benefit", "may_face_pressure"]
RunStatus = Literal["running", "done", "error"]

RELATIONSHIP_TYPES: tuple[str, ...] = get_args(RelationshipType)
EVENT_TYPES: tuple[str, ...] = get_args(EventType)
NEWS_EVENT_TYPES = tuple(t for t in EVENT_TYPES if t != "odds_move")
DIRECTIONS: tuple[str, ...] = get_args(Direction)

# Placeholders to tune, from the spec.
CONFIDENCE = {"filing": 0.9, "sector": 0.3}
DEFAULT_WEIGHT = 1

FIXTURES_DIR = Path(__file__).parent / "fixtures"
# Board fixtures live apart from the graph fixtures: fixture_tickers() globs FIXTURES_DIR, and a
# test compares every file there with the frontend's copy.
BOARD_FIXTURES_DIR = Path(__file__).parent / "board_fixtures"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompanyOut(_Strict):
    symbol: str
    name: str


class GraphCompanyOut(CompanyOut):
    """The searched company in a graph. Search results and boards keep the plain CompanyOut."""

    industry: str | None = None  # SEC's SIC description; None when SEC has not told us one


class NodeOut(_Strict):
    symbol: str
    name: str
    type: RelationshipType
    industry: str | None = None


class LinkOut(_Strict):
    source: str
    target: str
    type: RelationshipType
    summary: str
    evidence_url: str


class HighlightOut(_Strict):
    target: str
    direction: Direction
    event_type: EventType
    reason: str
    source_url: str
    event_time: str  # ISO 8601
    price_change_pct: float | None = None


class PairNewsItemOut(_Strict):
    source: Literal["news", "x"]
    title: str
    url: str
    published_at: str  # ISO 8601
    by: str | None = None  # the outlet for news, the @handle for X


class PairNewsResponse(_Strict):
    """GET /graph/{ticker}/news/{other}: recent news and X posts about the two companies together."""

    company: CompanyOut
    other: CompanyOut
    items: list[PairNewsItemOut]
    failed: list[str]  # sources that could not be searched this time: "news", "x"


class GraphResponse(_Strict):
    company: GraphCompanyOut
    status: RunStatus
    # The link run's own status. `status` stays "running" while the news run that follows the links
    # is going, so a client that only waits for links reads this: "done" here with "running" above
    # means every link is in and only highlights may still arrive.
    links_status: RunStatus | None = None
    nodes: list[NodeOut]
    links: list[LinkOut]
    highlights: list[HighlightOut]


class BoardMemberOut(_Strict):
    id: str  # "cik-" + the person's 10-digit SEC CIK: the same person has the same id on every board
    name: str
    role: str  # the officer title when the director is also an officer, otherwise "Director"
    evidence_url: str
    filed_at: str  # ISO date of the Form 3 or 4 the seat comes from


class BoardResponse(_Strict):
    company: CompanyOut
    status: RunStatus
    members: list[BoardMemberOut]


def load_fixture(ticker: str) -> GraphResponse | None:
    """The fake-mode response for a ticker, or None when there is no fixture for it."""
    path = FIXTURES_DIR / f"{ticker.upper()}.json"
    if not path.is_file():
        return None
    return GraphResponse.model_validate(json.loads(path.read_text()))


def fixture_tickers() -> list[str]:
    return sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))


def load_board_fixture(ticker: str) -> BoardResponse | None:
    """The fake-mode board for a ticker, or None when there is no fixture for it."""
    path = BOARD_FIXTURES_DIR / f"{ticker.upper()}.json"
    if not path.is_file():
        return None
    return BoardResponse.model_validate(json.loads(path.read_text()))


def board_fixture_tickers() -> list[str]:
    return sorted(p.stem for p in BOARD_FIXTURES_DIR.glob("*.json"))
