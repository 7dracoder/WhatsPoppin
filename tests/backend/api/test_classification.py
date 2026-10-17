from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.classification import MAX_ITEMS, get_classify_fn, router
from backend.api.deps import get_sentiment_store
from backend.classification.sentiment_cache import CachedSentiment, content_key
from backend.classification import ClassificationResult, JevError

app = FastAPI()
app.include_router(router)


def fake_result(mode, label):
    return ClassificationResult(mode=mode, label=label, raw={})


def post(fn, **body):
    app.dependency_overrides[get_classify_fn] = lambda: fn
    try:
        return TestClient(app).post("/classification/query", json=body)
    finally:
        app.dependency_overrides.clear()


def items(n):
    return [{"title": f"t{i}", "text": None} for i in range(n)]


def test_sentiment_percentage_positive():
    labels = iter(["positive", "negative", "positive", "neutral"])
    fn = lambda title, text, spec: fake_result("sentiment", next(labels))
    resp = post(fn, items=items(4), mode="sentiment")
    assert resp.status_code == 200
    assert resp.json()["percentage"] == 50 and resp.json()["n"] == 4


def test_boolean_uses_query_as_question():
    seen = []

    def fn(title, text, spec):
        seen.append(spec.question)
        return fake_result("boolean", "yes")

    resp = post(fn, items=items(2), mode="boolean", query="Is it about chips?")
    assert resp.json()["percentage"] == 100 and seen == ["Is it about chips?"] * 2


def test_boolean_requires_query():
    assert post(lambda *a: None, items=items(1), mode="boolean").status_code == 400


def test_item_cap():
    resp = post(lambda *a: fake_result("sentiment", "positive"), items=items(MAX_ITEMS + 1), mode="sentiment")
    assert resp.status_code == 422


def test_all_failures_return_502():
    def fn(*a):
        raise JevError("no key")

    assert post(fn, items=items(3), mode="sentiment").status_code == 502


def test_partial_failures_are_skipped():
    def fn(title, text, spec):
        if title == "t1":
            raise JevError("x")
        return fake_result("sentiment", "positive")

    resp = post(fn, items=items(2), mode="sentiment")
    assert resp.json()["n"] == 1 and resp.json()["percentage"] == 100


class FakeStore:
    """In-memory stand-in for the Postgres cache (there's no async SQLite driver to test against)."""

    def __init__(self, data=None, fail=False):
        self.data = dict(data or {})
        self.fail = fail

    async def get_many(self, keys):
        if self.fail:
            raise RuntimeError("db down")
        return {k: self.data[k] for k in keys if k in self.data}

    async def put_many(self, entries):
        if self.fail:
            raise RuntimeError("db down")
        self.data.update(entries)


def post_sentiment(fn, store=None, **body):
    app.dependency_overrides[get_classify_fn] = lambda: fn
    # Always override: otherwise a developer's DATABASE_URL would make these tests hit the real DB.
    app.dependency_overrides[get_sentiment_store] = lambda: store
    try:
        return TestClient(app).post("/classification/sentiment", json=body)
    finally:
        app.dependency_overrides.clear()


def test_sentiment_returns_one_label_per_item_in_order():
    labels = {"t0": "positive", "t1": "negative", "t2": "neutral"}
    fn = lambda title, text, spec: fake_result("sentiment", labels[title])
    resp = post_sentiment(fn, items=items(3))
    assert resp.status_code == 200
    assert [r["label"] for r in resp.json()] == ["positive", "negative", "neutral"]


def test_sentiment_failed_item_is_null_not_fatal():
    def fn(title, text, spec):
        if title == "t1":
            raise JevError("boom")
        return fake_result("sentiment", "positive")

    resp = post_sentiment(fn, items=items(3))
    assert [r["label"] for r in resp.json()] == ["positive", None, "positive"]


def test_sentiment_all_failed_is_502():
    def fn(title, text, spec):
        raise JevError("boom")

    assert post_sentiment(fn, items=items(2)).status_code == 502


def test_sentiment_rejects_empty_and_oversized_batches():
    fn = lambda title, text, spec: fake_result("sentiment", "neutral")
    assert post_sentiment(fn, items=[]).status_code == 400
    assert post_sentiment(fn, items=items(MAX_ITEMS + 1)).status_code == 422


def counting(label="positive"):
    calls = []

    def fn(title, text, spec):
        calls.append(title)
        return fake_result("sentiment", label)

    return fn, calls


def test_second_request_is_served_from_cache_without_calling_jev():
    store = FakeStore()
    fn, calls = counting("negative")
    first = post_sentiment(fn, store, items=items(2)).json()
    second = post_sentiment(fn, store, items=items(2)).json()
    assert calls == ["t0", "t1"]  # Jev was only called on the first request
    assert [r["cached"] for r in first] == [False, False]
    assert [r["cached"] for r in second] == [True, True]
    assert [r["label"] for r in second] == ["negative", "negative"]


def test_only_uncached_items_are_sent_to_jev():
    store = FakeStore({content_key("t0", None): CachedSentiment("positive", 0.9)})
    fn, calls = counting("neutral")
    resp = post_sentiment(fn, store, items=items(2)).json()
    assert calls == ["t1"]
    assert [(r["label"], r["cached"]) for r in resp] == [("positive", True), ("neutral", False)]


def test_repeated_text_in_one_batch_is_classified_once():
    fn, calls = counting()
    resp = post_sentiment(fn, FakeStore(), items=[{"title": "same", "text": None}] * 3).json()
    assert calls == ["same"] and [r["label"] for r in resp] == ["positive"] * 3


def test_failed_classifications_are_not_cached():
    store = FakeStore()

    def fn(title, text, spec):
        if title == "t1":
            raise JevError("boom")
        return fake_result("sentiment", "positive")

    post_sentiment(fn, store, items=items(2))
    assert set(store.data) == {content_key("t0", None)}  # t1 will be retried next time


def test_cache_outage_degrades_to_uncached_not_an_error():
    fn, calls = counting()
    resp = post_sentiment(fn, FakeStore(fail=True), items=items(2))
    assert resp.status_code == 200 and calls == ["t0", "t1"]


def test_content_key_depends_on_title_and_text_but_not_url():
    assert content_key("a", "b") == content_key("a", "b")
    assert content_key("a", "b") != content_key("a", "c")
    assert content_key("ab", "c") != content_key("a", "bc")
    assert content_key("a", None) == content_key("a", "")
