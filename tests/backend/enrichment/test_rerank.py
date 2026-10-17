from backend.classification.primitives import NoulAnswer
from backend.enrichment import rerank as rr


def candidates(n):
    return [{"market_id": f"M{i}", "title": f"title {i}", "event_title": f"event {i}"} for i in range(n)]


def fake_jev(score_of):
    calls = []

    def call(state, questions):
        calls.append((state, questions))
        return {qid: NoulAnswer(noul=score_of(q.instructions)) for qid, q in questions.items()}

    call.calls = calls
    return call


def test_ranks_by_relevance_and_drops_weak_matches():
    scores = {"event 0": 0.2, "event 1": 0.9, "event 2": 0.6}
    jev = fake_jev(lambda text: next(v for k, v in scores.items() if k in text))
    ranked = rr.rerank("NVIDIA Corporation", ["Semiconductors"], candidates(3), call_jev_fn=jev)
    assert [(m["market_id"], s) for m, s in ranked] == [("M1", 0.9), ("M2", 0.6)]
    state, questions = jev.calls[0]
    assert "NVIDIA Corporation" in state and "Semiconductors" in state
    assert "'event 1 / title 1'" in questions["m1"].instructions


def test_large_candidate_lists_are_split_into_batches():
    jev = fake_jev(lambda text: 0.7)
    ranked = rr.rerank("X", [], candidates(rr.BATCH + 10), call_jev_fn=jev)
    assert len(jev.calls) == 2 and len(ranked) == rr.BATCH + 10


def test_ties_keep_the_tag_order_and_empty_needs_no_call():
    jev = fake_jev(lambda text: 0.8)
    assert [m["market_id"] for m, _ in rr.rerank("X", [], candidates(3), call_jev_fn=jev)] == ["M0", "M1", "M2"]
    assert rr.rerank("X", [], [], call_jev_fn=jev) == [] and len(jev.calls) == 1
