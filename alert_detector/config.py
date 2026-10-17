"""Detector and baseline-job settings: defaults below, each overridable in .env by its upper-case name.

Lower the thresholds (e.g. Z_MIN=1.5, BURST_RATIO=2) to get alerts during a demo.
"""

import os
from dataclasses import dataclass, fields

INGESTION_RETENTION_MIN = 30  # ingestion keeps 30 minutes of live rows


@dataclass(frozen=True)
class Config:
    poll_s: float = 1               # how often to read new rows
    window_min: float = 5           # W: the signal window (now - W, now]
    lookback_min: float = 10        # how old a quote may be and still count as "the price"
    stale_s: float = 120            # no new data for this long = ingestion is down
    near_close_min: float = 15      # skip markets closing sooner than this
    max_spread: float = 0.10        # wider bid/ask spread = untrustworthy quote
    sigma_floor: float = 0.01
    min_sigma_samples: int = 30
    z_min: float = 3
    min_baseline_min: float = 60    # minutes of history a volume baseline needs
    vol_base_floor: float = 50
    burst_ratio: float = 5
    min_burst_notional: float = 300
    whale_floor: float = 500
    whale_thin: float = 1000
    min_whale_samples: int = 50
    min_imb_notional: float = 300
    min_imb_orders: int = 5
    imb_min: float = 0.6
    cooldown_min: float = 10
    # Baseline job (python -m baselines)
    baseline_days: float = 3            # history fetched per market
    baseline_refresh_hours: float = 8   # recompute a market's baseline after this long
    baseline_sweep_min: float = 5       # how often to look for new or stale markets

    @property
    def window_s(self) -> float:
        return self.window_min * 60

    @property
    def lookback_s(self) -> float:
        return self.lookback_min * 60

    @property
    def history_s(self) -> float:
        """Live data the detector keeps in memory: the window plus room to look back from its start."""
        return self.window_s + self.lookback_s + 5 * 60

    @property
    def cooldown_s(self) -> float:
        return self.cooldown_min * 60


def load() -> Config:
    values = {}
    for f in fields(Config):
        raw = os.environ.get(f.name.upper())
        if raw:
            values[f.name] = f.type(raw)  # the annotation (float/int), not the default's type
    cfg = Config(**values)
    if cfg.history_s / 60 > INGESTION_RETENTION_MIN:
        raise SystemExit(f"WINDOW_MIN + LOOKBACK_MIN too large: needs {cfg.history_s / 60:.0f} min of live "
                         f"data, ingestion keeps {INGESTION_RETENTION_MIN}")
    return cfg
