"""The signal maths as pure functions: plain inputs, no I/O, `now` passed in.

Times are Unix seconds (the platforms' timestamps). Prices are 0-1 YES probabilities;
changes are absolute points (0.05 = 5 points). Money is dollars.

Inputs:
- quotes: sorted list of (t, mid) from good quotes only (the last ~20 minutes)
- snapshots: sorted list of times where a reconnect catch-up row arrived (data gap)
- trades: sorted list of Trade
- b: a market_baselines row ("normal" from days of history), or None if not computed yet
"""

from bisect import bisect_right
from dataclasses import dataclass
from typing import NamedTuple

PINNED_LOW, PINNED_HIGH = 0.03, 0.97
INF = float("inf")


class Trade(NamedTuple):
    t: float
    notional: float
    side: str  # outcome the taker bought: 'yes' | 'no'
    yes_price: float
    is_block: bool


Order = Trade  # an aggregated order has the same fields


# --- Quotes -------------------------------------------------------------------
def is_good_quote(bid, ask, bid_size, ask_size, max_spread: float) -> bool:
    """Both sides have real orders and the spread is narrow enough to trust the midpoint."""
    if None in (bid, ask, bid_size, ask_size):
        return False
    return bid_size > 0 and ask_size > 0 and ask - bid <= max_spread


def midpoint(bid: float, ask: float) -> float:
    return (bid + ask) / 2


def mid_at(quotes: list, t: float, lookback_s: float) -> float | None:
    """Mid of the latest good quote at or before t, if it's at most lookback_s old.

    Quiet markets have no row exactly at t, so we take the last known price,
    but not one so old that it no longer says anything about t.
    """
    i = bisect_right(quotes, (t, INF)) - 1
    if i < 0:
        return None
    quote_t, mid = quotes[i]
    return mid if t - quote_t <= lookback_s else None


def has_snapshot(snapshots: list, start: float, end: float) -> bool:
    """Any reconnect gap in (start, end]?"""
    i = bisect_right(snapshots, start)
    return i < len(snapshots) and snapshots[i] <= end


# --- ① Price move ---------------------------------------------------------------
@dataclass
class PriceMove:
    mid_before: float | None = None
    mid_now: float | None = None
    change: float | None = None
    sigma: float | None = None
    samples: int = 0
    z: float | None = None
    fired: bool = False
    skip: str | None = None


def baseline_sigma(b, cfg) -> tuple[float | None, int] | None:
    """Typical 5-minute move from a market_baselines row: (sigma, samples), or None without a baseline.

    sigma is None when there were too few samples. The floor stops a quiet market
    turning a 1-point blip into a huge z.
    """
    if b is None:
        return None
    if b.sigma_5m is None or b.sigma_samples < cfg.min_sigma_samples:
        return None, b.sigma_samples
    return max(float(b.sigma_5m), cfg.sigma_floor), b.sigma_samples


def price_move(quotes, snapshots, now: float, cfg, sigma: tuple | None) -> PriceMove:
    """5-minute midpoint change as a z-score. `sigma` is baseline_sigma(...)."""
    w = cfg.window_s
    r = PriceMove(mid_now=mid_at(quotes, now, cfg.lookback_s),
                  mid_before=mid_at(quotes, now - w, cfg.lookback_s))
    if r.mid_now is None or r.mid_before is None:
        r.skip = "no recent good quote"
        return r
    r.change = r.mid_now - r.mid_before
    if has_snapshot(snapshots, now - w, now):
        r.skip = "reconnect gap in window"
        return r
    if max(r.mid_now, r.mid_before) <= PINNED_LOW or min(r.mid_now, r.mid_before) >= PINNED_HIGH:
        r.skip = "pinned near 0 or 1"
        return r
    if sigma is None:
        r.skip = "no baseline yet"
        return r
    r.sigma, r.samples = sigma
    if r.sigma is None:
        r.skip = f"thin history: {r.samples}/{cfg.min_sigma_samples} samples"
        return r
    r.z = r.change / r.sigma
    r.fired = abs(r.z) >= cfg.z_min
    return r


# --- Trades -----------------------------------------------------------------------
def notional(count: float, yes_price: float, taker_side: str) -> float:
    """Dollars the taker paid. 10,000 contracts at $0.01 is $100, not 10,000."""
    taker_price = yes_price if taker_side == "yes" else 1 - yes_price
    return count * taker_price


def trades_between(trades: list, start: float, end: float) -> list:
    """Trades with start < t <= end."""
    return trades[bisect_right(trades, (start, INF)):bisect_right(trades, (end, INF))]


def aggregate_orders(trades: list) -> list:
    """One large order fills as several trades with the same time and side; merge them."""
    orders: dict[tuple, Order] = {}
    for tr in trades:
        key = (tr.t, tr.side)
        prev = orders.get(key)
        orders[key] = tr if prev is None else prev._replace(
            notional=prev.notional + tr.notional, is_block=prev.is_block or tr.is_block)
    return list(orders.values())


# --- ② Volume burst --------------------------------------------------------------
@dataclass
class VolumeBurst:
    window_notional: float = 0.0
    baseline: float | None = None
    ratio: float | None = None
    fired: bool = False
    skip: str | None = None


def baseline_volume(b) -> tuple[float | None, float] | None:
    """Normal $ per window from a market_baselines row: (volume, history_minutes), or None."""
    if b is None:
        return None
    return (None if b.volume_per_window is None else float(b.volume_per_window)), float(b.history_minutes)


def volume_burst(window_trades, baseline: tuple | None, cfg) -> VolumeBurst:
    """`baseline` is baseline_volume(...)."""
    r = VolumeBurst(window_notional=sum(tr.notional for tr in window_trades))
    if baseline is None:
        r.skip = "no baseline yet"
        return r
    r.baseline, history_min = baseline
    if r.baseline is None or history_min < cfg.min_baseline_min:
        r.baseline = None
        r.skip = f"thin history: {history_min:.0f}/{cfg.min_baseline_min:.0f} min"
        return r
    r.ratio = r.window_notional / max(r.baseline, cfg.vol_base_floor)
    r.fired = r.ratio >= cfg.burst_ratio and r.window_notional >= cfg.min_burst_notional
    return r


# --- ③ Whale trade ----------------------------------------------------------------
@dataclass
class Whale:
    notional: float | None = None
    side: str | None = None
    is_block: bool = False
    threshold: float | None = None
    fired: bool = False
    skip: str | None = None


def whale_threshold(b, cfg) -> float | None:
    """Order size that counts as a whale, from a market_baselines row (None without a baseline).

    The baseline's p99 comes from history before now, so a whale never raises its own bar.
    """
    if b is None:
        return None
    if b.whale_p99 is None or b.whale_orders < cfg.min_whale_samples:
        return cfg.whale_thin
    return max(float(b.whale_p99), cfg.whale_floor)


def whale(window_orders, threshold: float | None) -> Whale:
    """`threshold` is whale_threshold(...)."""
    r = Whale(threshold=threshold)
    if threshold is None:
        r.skip = "no baseline yet"
        return r
    if not window_orders:
        r.skip = "no trades in window"
        return r
    biggest = max(window_orders, key=lambda o: o.notional)
    r.notional, r.side, r.is_block = biggest.notional, biggest.side, biggest.is_block
    r.fired = biggest.notional > threshold
    return r


# --- ④ Aggression imbalance ----------------------------------------------------------
@dataclass
class Imbalance:
    yes_notional: float = 0.0
    no_notional: float = 0.0
    orders: int = 0
    value: float | None = None  # 0 = balanced, 1 = all one side
    side: str | None = None
    skip: str | None = None


def imbalance(window_orders, cfg) -> Imbalance:
    r = Imbalance(yes_notional=sum(o.notional for o in window_orders if o.side == "yes"),
                  no_notional=sum(o.notional for o in window_orders if o.side == "no"),
                  orders=len(window_orders))
    total = r.yes_notional + r.no_notional
    # Small samples mislead: 2 trades both YES would look "100% one-sided".
    if total < cfg.min_imb_notional or r.orders < cfg.min_imb_orders:
        r.skip = f"too little flow (${total:.0f}, {r.orders} orders)"
        return r
    r.value = abs(r.yes_notional / total - 0.5) * 2
    r.side = "yes" if r.yes_notional > r.no_notional else "no"
    return r
