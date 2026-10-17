"""Per-market rolling buffers, filled from market_prices / market_trades rows."""

from bisect import bisect_left, insort
from dataclasses import dataclass, field

from alert_detector import signals


@dataclass
class MarketBuffer:
    quotes: list = field(default_factory=list)     # (t, mid), good quotes only, sorted
    snapshots: list = field(default_factory=list)  # times of reconnect catch-up rows, sorted
    trades: list = field(default_factory=list)     # signals.Trade, sorted by t
    first_seen: float | None = None
    last_seen: float = 0.0


class State:
    def __init__(self, cfg):
        self.cfg = cfg
        self.markets: dict[tuple, MarketBuffer] = {}  # by (source, market_id)
        self.latest_ts = 0.0  # newest Kalshi timestamp seen, for the freshness check
        self.last_price_id = 0
        self.last_trade_id = 0

    def add_price(self, r) -> tuple:
        """r has id, source, market_id, timestamp, yes_bid, yes_ask, yes_bid_size, yes_ask_size, snapshot."""
        key = (r.source, r.market_id)
        buf = self.markets.setdefault(key, MarketBuffer())
        t = r.timestamp.timestamp()
        if r.snapshot:
            # A snapshot is Kalshi's current state, sent after the ingestion
            # reconnects; its own timestamp can be hours old. Place it at the
            # moment we learned it (the newest Kalshi time seen so far) and
            # remember that moment as a data gap.
            t = max(t, self.latest_ts)
            insort(buf.snapshots, t)
        bid, ask = _f(r.yes_bid), _f(r.yes_ask)
        if signals.is_good_quote(bid, ask, _f(r.yes_bid_size), _f(r.yes_ask_size), self.cfg.max_spread):
            insort(buf.quotes, (t, signals.midpoint(bid, ask)))
        self._seen(buf, t)
        self.last_price_id = max(self.last_price_id, r.id)
        return key

    def add_trade(self, r) -> tuple:
        """r has id, source, market_id, timestamp, yes_price, count, taker_side, is_block_trade."""
        key = (r.source, r.market_id)
        buf = self.markets.setdefault(key, MarketBuffer())
        t = r.timestamp.timestamp()
        yes_price = float(r.yes_price)
        insort(buf.trades, signals.Trade(t, signals.notional(float(r.count), yes_price, r.taker_side),
                                         r.taker_side, yes_price, bool(r.is_block_trade)))
        self._seen(buf, t)
        self.last_trade_id = max(self.last_trade_id, r.id)
        return key

    def _seen(self, buf: MarketBuffer, t: float) -> None:
        buf.first_seen = t if buf.first_seen is None else min(buf.first_seen, t)
        buf.last_seen = max(buf.last_seen, t)
        self.latest_ts = max(self.latest_ts, t)

    def prune(self, now: float) -> None:
        """Drop data older than the history we need; forget markets with nothing left."""
        cutoff = now - self.cfg.history_s
        for key in list(self.markets):
            buf = self.markets[key]
            del buf.quotes[:bisect_left(buf.quotes, (cutoff,))]
            del buf.snapshots[:bisect_left(buf.snapshots, cutoff)]
            del buf.trades[:bisect_left(buf.trades, (cutoff,))]
            if buf.last_seen < cutoff:
                del self.markets[key]


def _f(value) -> float | None:
    return None if value is None else float(value)
