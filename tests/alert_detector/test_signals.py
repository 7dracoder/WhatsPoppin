from datetime import datetime, timezone
from decimal import Decimal
from itertools import count
from types import SimpleNamespace

import pytest

from alert_detector import signals as sig
from alert_detector.config import Config
from alert_detector.detector import Detector, Evaluation
from alert_detector.state import State

CFG = Config()
NOW = 1_800_000_000.0
_ids = count(1)


def _dt(t):
    return datetime.fromtimestamp(t, tz=timezone.utc)


def price_row(market_id, t, mid, snapshot=False, size=10, source="kalshi"):
    return SimpleNamespace(id=next(_ids), source=source, market_id=market_id, timestamp=_dt(t),
                           yes_bid=mid - 0.01, yes_ask=mid + 0.01,
                           yes_bid_size=size, yes_ask_size=size, snapshot=snapshot)


def flat_quotes(mid=0.5, until=NOW - 60):
    """A quote every 5 minutes across the live history the detector keeps, all at the same mid."""
    return [(t, mid) for t in range(int(NOW - CFG.history_s), int(until), 300)]


def baseline(sigma=0.01, samples=800, volume=50.0, minutes=3 * 24 * 60, p99=40.0, orders=600):
    """A market_baselines row: "normal" computed from days of history."""
    return SimpleNamespace(sigma_5m=sigma, sigma_samples=samples, volume_per_window=volume,
                           history_minutes=minutes, whale_p99=p99, whale_orders=orders,
                           computed_at=_dt(NOW - 3600), method="test")


SIGMA = (0.01, 800)  # baseline_sigma(baseline(), CFG)


def trade(t, notional, side="yes", block=False):
    return sig.Trade(t, notional, side, 0.5, block)


def setup_detector(moves: dict, event="EV", close_in=86400, now=NOW):
    """Markets with a flat 0.50 history that jump to `moves[market]` 30 s before now.

    Keys of `moves` are a market id (source kalshi) or a (source, market_id) pair.
    """
    state = State(CFG)
    detector = Detector(CFG, state)
    for key, move_to in moves.items():
        source, market_id = key if isinstance(key, tuple) else ("kalshi", key)
        for t, mid in flat_quotes(until=now - 60):
            state.add_price(price_row(market_id, t, mid, source=source))
        state.add_price(price_row(market_id, now - 30, move_to, source=source))
        detector.markets[(source, market_id)] = {
            "source": source, "market_id": market_id, "event_id": event, "title": "Title",
            "outcome_label": market_id, "close_time": _dt(now + close_in)}
        detector.baselines[(source, market_id)] = baseline()
    return state, detector


def K(market_id, source="kalshi"):
    return (source, market_id)


# --- Quotes ---------------------------------------------------------------------
def test_empty_side_ignored():
    assert not sig.is_good_quote(0.40, 0.50, 0, 10, CFG.max_spread)


def test_wide_spread_ignored():
    assert not sig.is_good_quote(0.30, 0.50, 10, 10, CFG.max_spread)
    assert sig.is_good_quote(0.45, 0.50, 10, 10, CFG.max_spread)


def test_stale_quote_gives_no_mid():
    quotes = [(0.0, 0.5)]
    assert sig.mid_at(quotes, CFG.lookback_s, CFG.lookback_s) == 0.5
    assert sig.mid_at(quotes, CFG.lookback_s + 1, CFG.lookback_s) is None


def test_quiet_market_uses_last_value_before_t():
    quotes = [(0.0, 0.40), (100.0, 0.50), (400.0, 0.60)]
    assert sig.mid_at(quotes, 250.0, CFG.lookback_s) == 0.50


# --- ① Price move ------------------------------------------------------------------
def test_snapshot_in_window_skips_price_signal():
    quotes = flat_quotes() + [(NOW - 30, 0.60)]
    r = sig.price_move(quotes, [NOW - 60], NOW, CFG, SIGMA)
    assert r.skip == "reconnect gap in window" and not r.fired


def test_pinned_price_skipped():
    quotes = flat_quotes(mid=0.02) + [(NOW - 30, 0.03)]
    assert sig.price_move(quotes, [], NOW, CFG, SIGMA).skip == "pinned near 0 or 1"


def test_thin_history_skips_price_signal():
    quotes = flat_quotes() + [(NOW - 30, 0.6)]
    r = sig.price_move(quotes, [], NOW, CFG, sig.baseline_sigma(baseline(samples=12), CFG))
    assert r.skip == "thin history: 12/30 samples" and r.z is None


def test_no_baseline_skips_every_baseline_signal():
    quotes = flat_quotes() + [(NOW - 30, 0.9)]
    assert sig.price_move(quotes, [], NOW, CFG, sig.baseline_sigma(None, CFG)).skip == "no baseline yet"
    assert sig.volume_burst([trade(NOW - 10, 5000)], sig.baseline_volume(None), CFG).skip == "no baseline yet"
    assert sig.whale([trade(NOW - 10, 5000)], sig.whale_threshold(None, CFG)).skip == "no baseline yet"


def test_sigma_floor_applied():
    quotes = flat_quotes() + [(NOW - 30, 0.52)]
    r = sig.price_move(quotes, [], NOW, CFG, sig.baseline_sigma(baseline(sigma=0.002), CFG))
    assert r.sigma == CFG.sigma_floor
    assert r.z == pytest.approx(2.0)  # 2 points / 1-point floor, not 10


# --- Trades --------------------------------------------------------------------------
def test_no_side_notional_uses_one_minus_yes_price():
    assert sig.notional(100, 0.30, "no") == pytest.approx(70.0)
    assert sig.notional(100, 0.30, "yes") == pytest.approx(30.0)


def test_same_timestamp_fills_aggregated():
    orders = sig.aggregate_orders([trade(1, 100), trade(1, 200, block=True), trade(1, 50, "no"), trade(2, 10)])
    assert len(orders) == 3
    big = next(o for o in orders if o.t == 1 and o.side == "yes")
    assert big.notional == 300 and big.is_block


def test_whale_threshold_is_baseline_p99_with_floor():
    assert sig.whale_threshold(baseline(p99=1392.0), CFG) == pytest.approx(1392.0)
    assert sig.whale_threshold(baseline(p99=40.0), CFG) == CFG.whale_floor  # $40 p99 -> $500 floor
    assert sig.whale([trade(NOW - 60, 5000)], sig.whale_threshold(baseline(p99=1392.0), CFG)).fired
    assert not sig.whale([trade(NOW - 60, 1200)], sig.whale_threshold(baseline(p99=1392.0), CFG)).fired


def test_thin_market_uses_whale_thin():
    assert sig.whale_threshold(baseline(orders=10), CFG) == CFG.whale_thin


def test_volume_burst_against_baseline():
    window = [trade(NOW - 60 * i, 100) for i in range(1, 7)]  # $600 in the window
    r = sig.volume_burst(window, sig.baseline_volume(baseline(volume=100.0)), CFG)
    assert r.ratio == pytest.approx(6.0) and r.fired
    thin = sig.volume_burst(window, sig.baseline_volume(baseline(volume=100.0, minutes=30)), CFG)
    assert thin.skip == "thin history: 30/60 min" and not thin.fired


def test_imbalance_needs_min_notional_and_orders():
    assert sig.imbalance([trade(i, 125) for i in range(4)], CFG).value is None  # $500 but 4 orders
    assert sig.imbalance([trade(i, 40) for i in range(5)], CFG).value is None   # 5 orders but $200
    r = sig.imbalance([trade(i, 80) for i in range(5)], CFG)
    assert r.value == 1.0 and r.side == "yes"


# --- Detector -------------------------------------------------------------------------
def test_imbalance_alone_does_not_escalate():
    detector = Detector(CFG, State(CFG))
    e = Evaluation("kalshi", "M", {})
    e.imbalance = sig.Imbalance(yes_notional=1000, orders=10, value=1.0, side="yes")
    detector._escalate(e)
    assert not e.candidate and e.reasons == []


def test_near_close_market_excluded():
    _, detector = setup_detector({"A": 0.60}, close_in=5 * 60)
    assert detector.evaluate(K("A"), NOW).skip == "closing soon"
    assert detector.process({K("A")}, NOW) == []


def test_strikes_grouped_by_event_with_related_markets():
    _, detector = setup_detector({"A": 0.60, "B": 0.55})
    alerts = detector.process({K("B")}, NOW)  # only B got a new row; A is found through the event
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["market_id"] == "A" and alert["source"] == "kalshi"  # the stronger move leads
    assert alert["reasons"] == ["price_move"] and alert["direction"] == "yes_up"
    assert [r["market_id"] for r in alert["context"]["related_markets"]] == ["B"]
    assert alert["summary"] == "[Kalshi] Title (A): YES rose from 0.500 to 0.600 (+10.0 pts, z=10.0) in 5 min."


def test_cooldown_blocks_repeats_but_lets_stronger_move_through():
    state, detector = setup_detector({"A": 0.55})
    assert len(detector.process({K("A")}, NOW)) == 1        # z=5, score ~1.67
    assert detector.process({K("A")}, NOW + 30) == []       # same move again: blocked
    state.add_price(price_row("A", NOW + 50, 0.70))         # z=20, score capped at 3 >= 1.5 x 1.67
    assert len(detector.process({K("A")}, NOW + 60)) == 1


def test_stale_ingestion_writes_no_alerts():
    _, detector = setup_detector({"A": 0.60})
    assert detector.process({K("A")}, NOW + CFG.stale_s + 60) == []
    assert detector.stale


def test_env_thresholds_accept_decimals(monkeypatch):
    from alert_detector import config
    monkeypatch.setenv("Z_MIN", "1.5")  # default is written as 3; must still parse as float
    monkeypatch.setenv("MIN_SIGMA_SAMPLES", "5")
    cfg = config.load()
    assert cfg.z_min == 1.5 and cfg.min_sigma_samples == 5


def test_sources_never_mix():
    """Same market id and event on two sources: separate state, separate alerts, separate cooldown."""
    state, detector = setup_detector({K("A"): 0.60, K("A", "polymarket"): 0.55})
    assert state.markets[K("A")] is not state.markets[K("A", "polymarket")]
    alerts = detector.process({K("A"), K("A", "polymarket")}, NOW)
    assert sorted(a["source"] for a in alerts) == ["kalshi", "polymarket"]
    for a in alerts:
        assert a["context"]["related_markets"] == []  # no grouping across sources
    poly = next(a for a in alerts if a["source"] == "polymarket")
    assert poly["mid_now"] == Decimal("0.55") and poly["summary"].startswith("[Polymarket] ")
    # Kalshi's cooldown doesn't block a new Polymarket alert, and vice versa.
    assert set(detector.cooldown) == {"kalshi:EV", "polymarket:EV"}


def test_market_without_baseline_never_alerts():
    _, detector = setup_detector({"A": 0.90})  # a 40-point jump
    detector.baselines.clear()
    e = detector.evaluate(K("A"), NOW)
    assert e.price.skip == "no baseline yet" and not e.candidate
    assert detector.process({K("A")}, NOW) == []
