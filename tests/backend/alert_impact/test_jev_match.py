import pytest

from backend.alert_impact import jev_match
from backend.classification import JevError
from backend.classification.primitives import NoulAnswer

MARKET = {"market_id": "KXAAAGASD-1", "title": "Will average gas prices be above $3.20?", "outcome_label": "Above 3.20",
          "event_title": "US gas prices tomorrow"}


def fake_jev(score_of):
    calls = []

    def call(state, questions):
        calls.append((state, questions))
        return {qid: NoulAnswer(noul=score_of(q.instructions)) for qid, q in questions.items()}

    call.calls = calls
    return call


def test_keeps_likely_companies_most_likely_first():
    scores = {"Exxon": 0.95, "Chevron": 0.9, "Apple": 0.1}
    jev = fake_jev(lambda text: next(v for k, v in scores.items() if k in text))
    companies = [("AAPL", "Apple Inc."), ("CVX", "Chevron Corporation"), ("XOM", "Exxon Mobil Corporation")]
    assert jev_match.affected_companies(MARKET, companies, call_jev_fn=jev) == [("XOM", 0.95), ("CVX", 0.9)]
    state, questions = jev.calls[0]
    assert "US gas prices tomorrow" in state and "Above 3.20" in state
    assert len(questions) == 3


def test_companies_are_asked_in_batches():
    jev = fake_jev(lambda text: 0.6)
    companies = [(f"C{i}", f"Company {i}") for i in range(121)]
    assert len(jev_match.affected_companies(MARKET, companies, call_jev_fn=jev)) == 121
    assert sorted(len(q) for _, q in jev.calls) == [21, 50, 50]


def test_errors_propagate_for_the_worker_to_fall_back():
    def failing(state, questions):
        raise JevError("402")

    with pytest.raises(JevError):
        jev_match.affected_companies(MARKET, [("XOM", "Exxon")], call_jev_fn=failing)
