"""`/evidence` router: on-demand supporting evidence for a ticker (news today).

No existing route fetched news on demand (poll_news is built for scheduled
polling), so this wraps it the way api/twitter.py wraps twitter_lookup.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.deps import get_news_gateway, get_news_store
from backend.evidence.news import news_for
from backend.evidence.news_cache import NewsStore
from backend.ingestion.news_api import ContentItem, NewsApiError, NewsApiGateway

router = APIRouter(prefix="/evidence", tags=["evidence"])
logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK = timedelta(days=30)
MOCK_ARTICLE_COUNT = 6
MOCK_MARKER = "[MOCK]"


def _mock_news(ticker: str, start: datetime, end: datetime) -> list[ContentItem]:
    """Deterministic placeholder items (no randomness, so the UI and tests are
    stable). Titles carry MOCK_MARKER so nobody mistakes them for real news.
    """
    step = (end - start) / MOCK_ARTICLE_COUNT
    return [
        ContentItem(
            source="news",
            id=f"mock://news/{ticker}/{i}",
            author="Mock Wire",
            title=f"{MOCK_MARKER} {ticker} headline #{i + 1}",
            text=f"Placeholder article about {ticker}; real news is unavailable.",
            entities=[ticker],
            url=f"mock://news/{ticker}/{i}",
            published_at=end - step * i - step / 2,
        )
        for i in range(MOCK_ARTICLE_COUNT)
    ]


@router.get("/news", response_model=list[ContentItem])
async def get_news(
    ticker: str,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    gateway: NewsApiGateway | None = Depends(get_news_gateway),
    store: NewsStore | None = Depends(get_news_store),
) -> list[ContentItem]:
    """News mentioning the ticker (or its aliases) in the window, newest first. Answered from the
    news cache when the same day-bucketed window was fetched before; `python -m backend.evidence`
    pre-fills it for the demo tickers. Mock items (titles marked [MOCK]) only when there is no
    key, or NewsAPI fails and nothing is cached for the ticker.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")
    end = end or datetime.now(timezone.utc)
    start = start or end - DEFAULT_LOOKBACK

    if gateway is None:
        return _mock_news(ticker, start, end)
    try:
        items, _ = await news_for(ticker, start, end, gateway, store)
        return items
    except NewsApiError as exc:
        # A demo shouldn't break on a rate limit or bad key; log and degrade.
        logger.warning("NewsAPI failed (%s); serving mock news", exc)
        return _mock_news(ticker, start, end)
