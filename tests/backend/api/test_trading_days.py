from datetime import datetime, timezone

from backend.api.trading_days import parse_end, previous_day, snap_to_session, with_fallback

SUNDAY = datetime(2026, 10, 11, 3, 12, tzinfo=timezone.utc)
FRIDAY_CLOSE = datetime(2026, 10, 10, 0, 0, tzinfo=timezone.utc)  # Saturday 00:00 = end of Friday


def test_weekend_snaps_to_end_of_friday():
    assert snap_to_session(SUNDAY) == FRIDAY_CLOSE
    assert snap_to_session(datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)) == FRIDAY_CLOSE


def test_weekday_passes_through():
    wed = datetime(2026, 10, 7, 18, 30, tzinfo=timezone.utc)
    assert snap_to_session(wed) == wed


def test_previous_day_skips_the_weekend():
    monday = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
    assert previous_day(monday) == FRIDAY_CLOSE


def test_with_fallback_steps_back_until_there_are_bars():
    seen = []

    def fetch(at):
        seen.append(at)
        return [] if at >= FRIDAY_CLOSE else ["bar"]

    result, landed = with_fallback(SUNDAY, fetch, lambda r: not r)
    assert result == ["bar"]
    assert landed == datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)  # end of Thursday
    assert seen[0] == FRIDAY_CLOSE  # the weekend snapped first, then one step back


def test_with_fallback_gives_up_after_max_steps():
    result, _ = with_fallback(SUNDAY, lambda at: [], lambda r: not r)
    assert result == []


def test_parse_end_accepts_z_and_defaults_to_now():
    assert parse_end("2026-10-09T20:00:00Z") == datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
    assert parse_end(None).tzinfo is timezone.utc
