"""Alert impact reads (new_specs/alert-company-impact.md): recent alerts with the companies they
may affect, and the alerts that affected one company. Mounted in backend/main.py. Read-only:
`python -m backend.alert_impact` fills the tables.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.alert_impact import db
from backend.api.deps import get_db_engine

router = APIRouter(tags=["alerts"])


class AlertCompany(BaseModel):
    symbol: str
    name: str | None
    tier: int  # 1 = the market is about it, 2 = linked to a tier-1 company in the company graph, 3 = Jev
    matched: list[str]  # why: the tag (1), the tier-1 companies it links to (2), the market's tags (3)
    relevance: float | None = None  # tier 3: Jev's probability that the market matters to it


class AlertOut(BaseModel):
    id: int
    created_at: datetime
    source: str
    market_id: str
    title: str | None
    event_title: str | None
    url: str | None
    direction: str  # 'yes_up' | 'yes_down', from the alert detector
    summary: str
    score: float


class RecentAlertOut(AlertOut):
    companies: list[AlertCompany]


class CompanyAlertOut(AlertOut):
    tier: int
    matched: list[str]
    relevance: float | None = None


@router.get("/alerts/recent", response_model=list[RecentAlertOut])
async def recent_alerts(minutes: int = Query(60, ge=1, le=7 * 24 * 60), limit: int = Query(50, ge=1, le=200),
                        companies: int = Query(10, ge=1, le=50), engine: AsyncEngine = Depends(get_db_engine)):
    """Alerts from the last `minutes`, newest first, each with up to `companies` affected companies."""
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return await db.recent_alerts(engine, since, limit, companies)


@router.get("/entities/{company_id}/alerts", response_model=list[CompanyAlertOut])
async def company_alerts(company_id: str, minutes: int = Query(24 * 60, ge=1, le=30 * 24 * 60),
                         limit: int = Query(50, ge=1, le=200), engine: AsyncEngine = Depends(get_db_engine)):
    """Alerts that affected this company in the last `minutes`, newest first."""
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return await db.company_alerts(engine, company_id, since, limit)
