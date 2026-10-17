import json
from pathlib import Path

from backend.ingestion.alpaca.normalize import normalize_batch, normalize_bar

FIXTURES = Path(__file__).parent / "fixtures"


def _load_bars() -> list[dict]:
    return json.loads((FIXTURES / "sample_bars_response.json").read_text())["bars"]


def test_normalize_bar_maps_close_and_drops_ohl():
    raw = _load_bars()[0]
    point = normalize_bar("NVDA", raw)

    assert point.source == "alpaca"
    assert point.market_id == "NVDA"
    assert point.price_or_odds == 183.0  # the close ("c"), not open/high/low
    assert point.volume == 125000
    assert str(point.timestamp.year) == "2026"


def test_normalize_bar_raises_on_missing_close():
    raw = _load_bars()[2]  # fixture's third bar has no "c"
    try:
        normalize_bar("NVDA", raw)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "c (close)" in str(exc)


def test_normalize_batch_skips_malformed_bars():
    points = normalize_batch("NVDA", _load_bars())

    assert len(points) == 2
    assert all(p.market_id == "NVDA" for p in points)
