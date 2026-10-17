"""Maps a ZoomTier to the (start, end, timeframe) Alpaca's historical bars
endpoint needs. See new_specs/ingestion/alpaca.md Functional Requirement 4.
"""

from datetime import datetime, timedelta, timezone

from .models import ZoomTier

TIER_TIMEFRAME = {
    ZoomTier.RECENT: "1Min",
    ZoomTier.DAILY: "5Min",
    # Finer bars keep the chart from looking blocky. Row counts are per trading
    # session (6.5h): 5Min ~ 390 rows/week, 30Min ~ 270/month, 1Hour ~ 420/quarter.
    ZoomTier.WEEKLY: "5Min",
    ZoomTier.MONTHLY: "30Min",
    ZoomTier.QUARTERLY: "1Hour",
    ZoomTier.ALL_TIME: "1Day",
}

TIER_LOOKBACK = {
    ZoomTier.RECENT: timedelta(hours=6),
    ZoomTier.DAILY: timedelta(hours=24),
    ZoomTier.WEEKLY: timedelta(days=7),
    ZoomTier.MONTHLY: timedelta(days=30),
    ZoomTier.QUARTERLY: timedelta(days=90),
}

# Free (IEX) feed's historical coverage starts here; All-time just uses it as `start`.
ALL_TIME_START = datetime(2016, 1, 1, tzinfo=timezone.utc)


def tier_range(tier: ZoomTier, now: datetime) -> tuple[datetime, datetime]:
    """(start, end) for a tier, as of `now` (injectable for tests).

    `end` is always `now` for every tier, deliberately not shifted back
    by the free tier's ~15-minute delay — Alpaca's own response simply
    won't have bars for data it doesn't have yet, so the real delay shows
    up honestly as the last returned point's timestamp, rather than being
    hidden by requesting a pre-shrunk window (see alpaca.md FR 6).
    """
    if tier == ZoomTier.ALL_TIME:
        return ALL_TIME_START, now
    return now - TIER_LOOKBACK[tier], now


def tier_timeframe(tier: ZoomTier) -> str:
    return TIER_TIMEFRAME[tier]
