from datetime import datetime, timedelta, timezone

from backend.ingestion.alpaca.models import ZoomTier
from backend.ingestion.alpaca.tiers import ALL_TIME_START, tier_range, tier_timeframe

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)


def test_recent_tier_range_and_timeframe():
    start, end = tier_range(ZoomTier.RECENT, NOW)
    assert end == NOW  # not shrunk to hide the free-tier delay
    assert start == NOW - timedelta(hours=6)
    assert tier_timeframe(ZoomTier.RECENT) == "1Min"


def test_daily_tier():
    start, end = tier_range(ZoomTier.DAILY, NOW)
    assert (start, end) == (NOW - timedelta(hours=24), NOW)
    assert tier_timeframe(ZoomTier.DAILY) == "5Min"


def test_weekly_and_monthly_share_timeframe():
    assert tier_timeframe(ZoomTier.WEEKLY) == "5Min"
    assert tier_timeframe(ZoomTier.MONTHLY) == "30Min"
    weekly_start, _ = tier_range(ZoomTier.WEEKLY, NOW)
    monthly_start, _ = tier_range(ZoomTier.MONTHLY, NOW)
    assert weekly_start == NOW - timedelta(days=7)
    assert monthly_start == NOW - timedelta(days=30)
    assert monthly_start < weekly_start  # monthly is the wider range


def test_all_time_tier_starts_at_feed_coverage_start():
    start, end = tier_range(ZoomTier.ALL_TIME, NOW)
    assert start == ALL_TIME_START
    assert end == NOW
    assert tier_timeframe(ZoomTier.ALL_TIME) == "1Day"


def test_quarterly_is_90_days_of_hourly_bars():
    start, end = tier_range(ZoomTier.QUARTERLY, NOW)
    assert end - start == timedelta(days=90)
    assert tier_timeframe(ZoomTier.QUARTERLY) == "1Hour"
