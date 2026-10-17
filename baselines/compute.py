"""Baseline maths as pure functions: price history and trades in, "normal" numbers out.

Times are Unix seconds, prices 0-1 YES, money dollars. Trades are alert_detector.signals.Trade
(already in the YES point of view), so live and historical numbers are measured the same way.
"""

import statistics
from bisect import bisect_right
from dataclasses import dataclass

from alert_detector.signals import aggregate_orders


@dataclass
class History:
    """What a source fetcher returns for one market."""
    points: list           # sorted (t, mid or None); None = no trustworthy quote at that time
    step_s: float          # spacing of the price points
    lookback_s: float      # how long a point stays "the price" when the next one is missing
    trades: list           # signals.Trade, any order
    trades_from: float     # start of the period `trades` fully covers
    method: str


def sigma_from_points(points: list, window_s: float, step_s: float, lookback_s: float) -> tuple[float | None, int]:
    """Standard deviation of window_s midpoint changes, sampled every step_s. Returns (sigma, samples).

    The floor and the minimum sample count are applied by the detector, so they can be
    tuned without recomputing baselines.
    """
    if len(points) < 2:
        return None, 0
    times = [p[0] for p in points]

    def mid_at(t: float) -> float | None:
        # Latest point at or before t, if recent enough. Bisect on times only:
        # a None mid can't be compared inside a tuple.
        i = bisect_right(times, t) - 1
        if i < 0 or t - times[i] > lookback_s:
            return None
        return points[i][1]

    changes = []
    t, end = times[0] + window_s, times[-1]
    while t <= end:
        before, after = mid_at(t - window_s), mid_at(t)
        if before is not None and after is not None:
            changes.append(after - before)
        t += step_s
    if not changes:
        return None, 0
    return statistics.pstdev(changes), len(changes)


def volume_per_window(trades: list, span_s: float, window_s: float) -> float | None:
    """Average $ traded per window over span_s (empty windows count as $0)."""
    if span_s <= 0:
        return None
    return sum(t.notional for t in trades) / (span_s / window_s)


def p99(sizes: list[float]) -> float | None:
    if not sizes:
        return None
    if len(sizes) == 1:
        return sizes[0]
    return statistics.quantiles(sizes, n=100, method="inclusive")[98]


def whale_from_trades(trades: list) -> tuple[float | None, int]:
    """99th-percentile order size (fills with the same time and side are one order) and order count."""
    sizes = [o.notional for o in aggregate_orders(trades)]
    return p99(sizes), len(sizes)


def from_hourly(rows: list, window_s: float) -> tuple[float | None, float, float | None, int]:
    """Volume and whale numbers from market_hourly rows.

    Only hours we were watching count (a quote or a trade was recorded), so hours when
    the worker was down don't look like quiet hours. Returns
    (volume_per_window, history_minutes, whale_p99, whale_orders).
    """
    watched = [r for r in rows if r.quote_rows > 0 or r.orders > 0]
    minutes = 60.0 * len(watched)
    if not watched:
        return None, 0.0, None, 0
    notional = sum(float(r.notional) for r in watched)
    sizes = [float(s) for r in watched for s in (r.order_sizes or [])]
    return notional / (minutes * 60 / window_s), minutes, p99(sizes), len(sizes)
