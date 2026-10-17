import pytest

from backend.classification import classifier
from backend.classification.modes import BooleanSpec
from backend.classification.primitives import NoulAnswer


def test_classify_dispatches_to_mode_and_returns_interpreted_result(monkeypatch):
    captured = {}

    def fake_call_jev(state, questions, *args, **kwargs):
        captured["state"] = state
        captured["questions"] = questions
        return {"main": NoulAnswer(noul=0.95)}

    monkeypatch.setattr(classifier, "call_jev", fake_call_jev)

    spec = BooleanSpec(question="is this bullish?")
    result = classifier.classify("Nvidia beats earnings", "Record quarter for NVDA.", spec)

    assert captured["state"] == "Nvidia beats earnings\n\nRecord quarter for NVDA."
    assert list(captured["questions"].keys()) == ["main"]
    assert result.mode == "boolean"
    assert result.label == "yes"
    assert result.probability == 0.95


def test_classify_uses_title_only_state_when_text_is_none(monkeypatch):
    captured = {}

    def fake_call_jev(state, questions, *args, **kwargs):
        captured["state"] = state
        return {"main": NoulAnswer(noul=0.5)}

    monkeypatch.setattr(classifier, "call_jev", fake_call_jev)

    classifier.classify("Just a title", None, BooleanSpec(question="q"))

    assert captured["state"] == "Just a title"


def test_classify_raises_on_unregistered_mode():
    class FakeSpec:
        mode = "not_a_real_mode"

    with pytest.raises(ValueError, match="Unregistered classification mode"):
        classifier.classify("title", "text", FakeSpec())
