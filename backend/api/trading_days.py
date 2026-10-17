"""Pick the window end for intraday charts: weekends snap to Friday's close, and a day with no
bars (a holiday) steps back a day, so "yesterday" on a Sunday shows Friday rather than nothing.
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TypeVar

T = TypeVar("T")

MAX_STEPS_BACK = 4  # Thu-Fri holiday + weekend is the longest run of closed days seen in practice


def snap_to_session(end: datetime) -> datetime:
    """A Saturday or Sunday becomes the end of Friday (Saturday 00:00 UTC), so a 24h window
    back from it covers Friday's session. Weekdays pass through untouched.
    """
    if end.weekday() < 5:
        return end
    saturday = end - timedelta(days=end.weekday() - 5)
    return saturday.replace(hour=0, minute=0, second=0, microsecond=0)


def previous_day(end: datetime) -> datetime:
    """One calendar day earlier, then snapped off a weekend."""
    return snap_to_session(end - timedelta(days=1))


def with_fallback(end: datetime, fetch: Callable[[datetime], T], is_empty: Callable[[T], bool]) -> tuple[T, datetime]:
    """`fetch(end)`, stepping back a day while the result is empty (at most MAX_STEPS_BACK times).
    Returns the result and the window end it came from.
    """
    end = snap_to_session(end)
    result = fetch(end)
    for _ in range(MAX_STEPS_BACK):
        if not is_empty(result):
            break
        end = previous_day(end)
        result = fetch(end)
    return result, end


def parse_end(end: str | None) -> datetime:
    """An ISO 8601 `end` query param (a trailing Z is fine), or now."""
    if not end:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
