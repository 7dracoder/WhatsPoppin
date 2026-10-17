from dataclasses import dataclass

from backend.classification import relevance
from backend.classification.modes import BooleanSpec
from backend.classification.results import ClassificationResult


@dataclass
class _Item:
    title: str
    text: str | None


def _fake_classify_returning(probability: float):
    captured = {}

    def fake(title, text, spec):
        captured["title"] = title
        captured["text"] = text
        captured["spec"] = spec
        return ClassificationResult(mode="boolean", label="yes", probability=probability, raw={})

    return fake, captured


def test_is_relevant_true_above_threshold(monkeypatch):
    fake, captured = _fake_classify_returning(0.9)
    monkeypatch.setattr(relevance, "classify", fake)

    assert relevance.is_relevant("title", "text", "NVDA") is True
    assert isinstance(captured["spec"], BooleanSpec)
    assert "NVDA" in captured["spec"].question


def test_is_relevant_false_below_threshold(monkeypatch):
    fake, _ = _fake_classify_returning(0.2)
    monkeypatch.setattr(relevance, "classify", fake)

    assert relevance.is_relevant("title", "text", "NVDA") is False


def test_is_relevant_respects_custom_threshold(monkeypatch):
    fake, _ = _fake_classify_returning(0.6)
    monkeypatch.setattr(relevance, "classify", fake)

    assert relevance.is_relevant("title", "text", "NVDA", threshold=0.5) is True
    assert relevance.is_relevant("title", "text", "NVDA", threshold=0.7) is False


def test_filter_relevant_splits_items_and_preserves_order(monkeypatch):
    items = [
        _Item(title="Nvidia beats earnings", text="..."),
        _Item(title="PyPI package nvidia-dali 1.0", text="broken page"),
        _Item(title="Nvidia export controls tighten", text="..."),
        _Item(title="Mac Studio review namedrops Nvidia once", text="..."),
    ]
    # relevant/irrelevant keyed by title, so each item gets its own decision
    decisions = {
        "Nvidia beats earnings": 0.95,
        "PyPI package nvidia-dali 1.0": 0.1,
        "Nvidia export controls tighten": 0.8,
        "Mac Studio review namedrops Nvidia once": 0.3,
    }

    def fake_classify(title, text, spec):
        return ClassificationResult(mode="boolean", label="yes", probability=decisions[title], raw={})

    monkeypatch.setattr(relevance, "classify", fake_classify)

    relevant, dropped = relevance.filter_relevant(items, "NVDA")

    assert [item.title for item in relevant] == ["Nvidia beats earnings", "Nvidia export controls tighten"]
    assert [item.title for item in dropped] == ["PyPI package nvidia-dali 1.0", "Mac Studio review namedrops Nvidia once"]


def test_filter_relevant_handles_empty_list(monkeypatch):
    monkeypatch.setattr(relevance, "classify", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))

    relevant, dropped = relevance.filter_relevant([], "NVDA")

    assert relevant == []
    assert dropped == []
