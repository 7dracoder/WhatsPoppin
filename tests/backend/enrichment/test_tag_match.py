import pytest

from backend.classification import JevError
from backend.classification.primitives import ChoiceAnswer
from backend.enrichment.tag_match import NONE, match_tags
from backend.entities import EntityMap, MapEntity

MAP = EntityMap(version=1, entities=[
    MapEntity(symbol="TSLA", name="Tesla, Inc.", category="company", aliases=["Tesla"]),
    MapEntity(symbol="Electric Vehicles", name="Electric Vehicles", category="sector"),
    MapEntity(symbol="Crude Oil", name="Crude Oil", category="resource"),
])


def fake_jev(picks: dict[str, tuple[str, float]]):
    """Answers every question; `picks` maps a tag to (option, probability) in whichever half has it."""
    calls = []

    def call(state, questions):
        calls.append((state, questions))
        answers = {}
        for qid, q in questions.items():
            tag = next(t for t in picks if repr(t) in q.instructions) if any(repr(t) in q.instructions
                                                                              for t in picks) else None
            option, p = picks.get(tag, (NONE, 0.9))
            if option not in q.criteria:
                option, p = NONE, 0.9
            answers[qid] = ChoiceAnswer(choice=option, probabilities={option: p}, confidence=p)
        return answers

    call.calls = calls
    return call


def test_exact_names_tickers_and_aliases_need_no_jev_call():
    jev = fake_jev({})
    result = match_tags(["tesla", "TSLA", "electric vehicles", " Crude Oil "], MAP, call_jev_fn=jev)
    assert {t: e.symbol for t, e in result.items()} == {
        "tesla": "TSLA", "TSLA": "TSLA", "electric vehicles": "Electric Vehicles", "Crude Oil": "Crude Oil"}
    assert jev.calls == []


def test_fuzzy_tags_go_to_one_jev_call_with_two_questions_each():
    jev = fake_jev({"ev": ("Electric Vehicles", 0.95), "oil prices": ("Crude Oil", 0.8)})
    result = match_tags(["ev", "oil prices", "banana bread"], MAP, call_jev_fn=jev)
    assert result["ev"].symbol == "Electric Vehicles" and result["oil prices"].symbol == "Crude Oil"
    assert result["banana bread"] is None
    (state, questions), = jev.calls
    assert len(questions) == 3 * 2  # one per tag per half of the map
    assert all(NONE in q.criteria for q in questions.values())


def test_low_confidence_pick_counts_as_no_match():
    jev = fake_jev({"ev": ("Electric Vehicles", 0.3)})
    assert match_tags(["ev"], MAP, call_jev_fn=jev)["ev"] is None


def test_jev_errors_propagate():
    def failing(state, questions):
        raise JevError("down")

    with pytest.raises(JevError):
        match_tags(["ev"], MAP, call_jev_fn=failing)
