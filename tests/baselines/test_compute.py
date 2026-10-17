from types import SimpleNamespace

import pytest

from alert_detector.signals import Trade
from baselines import compute
from baselines.sources import kalshi_points, kalshi_trade, polymarket_trade, us_points

W = 300  # 5-minute window


def test_sigma_from_regular_points():
    points = [(i * 300.0, 0.50 + (0.02 if i % 2 else 0.0)) for i in range(20)]  # alternates +2 / -2 pts
    sigma, samples = compute.sigma_from_points(points, W, 300, 600)
    assert samples == 19 and sigma == pytest.approx(0.02, rel=0.01)


def test_none_points_break_the_series():
    points = [(0.0, 0.50), (300.0, None), (600.0, 0.60)]
    sigma, samples = compute.sigma_from_points(points, W, 300, 600)
    assert samples == 0 and sigma is None  # every 5-min change touches the missing quote


def test_sparse_points_carry_forward_within_lookback():
    points = [(0.0, 0.40), (3600.0, 0.45)]  # nothing changed for an hour, then +5 pts
    sigma, samples = compute.sigma_from_points(points, W, 60, 6 * 3600)
    assert samples == 56 and sigma > 0  # 55 flat 5-min changes, one +5 pt change


def test_volume_per_window_counts_empty_windows():
    trades = [Trade(0, 600.0, "yes", 0.5, False)]
    assert compute.volume_per_window(trades, 3600, W) == pytest.approx(50.0)  # $600 over 12 windows
    assert compute.volume_per_window(trades, 0, W) is None


def test_whale_p99_aggregates_same_time_fills():
    trades = [Trade(i, 10.0, "yes", 0.5, False) for i in range(99)] + [Trade(1000, 500, "no", 0.5, False),
                                                                        Trade(1000, 500, "no", 0.5, False)]
    p99, orders = compute.whale_from_trades(trades)
    assert orders == 100  # the two $500 fills at t=1000 are one $1,000 order
    assert 10 < p99 <= 1000


def test_hourly_rollups_only_count_watched_hours():
    rows = [SimpleNamespace(quote_rows=40, orders=2, notional=120, order_sizes=[100, 20]),
            SimpleNamespace(quote_rows=5, orders=0, notional=0, order_sizes=[]),
            SimpleNamespace(quote_rows=0, orders=0, notional=0, order_sizes=[])]  # worker was down
    volume, minutes, p99, orders = compute.from_hourly(rows, W)
    assert minutes == 120 and orders == 2
    assert volume == pytest.approx(120 / 24)  # $120 over two watched hours = 24 windows


def test_kalshi_candles_to_points():
    candles = [{"end_period_ts": 60, "yes_bid": {"close_dollars": "0.40"}, "yes_ask": {"close_dollars": "0.42"}},
               {"end_period_ts": 120, "yes_bid": {"close_dollars": "0.00"}, "yes_ask": {"close_dollars": "0.42"}}]
    assert kalshi_points(candles, 0.10) == [(60.0, pytest.approx(0.41)), (120.0, None)]  # empty bid -> None


def test_kalshi_trade_uses_taker_outcome_side():
    t = kalshi_trade({"taker_outcome_side": "no", "yes_price_dollars": "0.30", "count_fp": "100.00",
                      "created_time": "2026-10-10T15:00:00Z"})
    assert t.side == "no" and t.notional == pytest.approx(70.0)


def test_polymarket_trade_on_outcome_1_converts_to_yes_view():
    t = polymarket_trade({"price": 0.30, "size": 100, "side": "BUY", "outcomeIndex": 1, "timestamp": 1})
    assert t.side == "no" and t.yes_price == pytest.approx(0.70) and t.notional == pytest.approx(30.0)
    assert polymarket_trade({"price": 0.30, "size": 100, "side": "SELL", "outcomeIndex": 0, "timestamp": 1}).side == "no"


def test_us_display_prices_to_mid():
    points = us_points([{"timestamp": 300, "longPrice": 0.588, "shortPrice": 0.413},
                        {"timestamp": 600, "longPrice": 0.90, "shortPrice": 0.50}], 0.10)
    assert points[0] == (300.0, pytest.approx((0.587 + 0.588) / 2))  # bid = 1 - shortPrice
    assert points[1] == (600.0, None)  # 40-point spread: untrustworthy
