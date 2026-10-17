import json

import httpx
import numpy as np
import pytest

from backend.llm import client as llm_client
from backend.llm.embeddings import BATCH_SIZE, EmbeddingError, embed, top_k


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("NICRRON_API_KEY", "n-key")
    monkeypatch.delenv("NICRRON_EMBED_MODEL", raising=False)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_embed_calls_nicrron_and_normalizes():
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen.update(url=str(request.url), auth=request.headers["Authorization"], **body)
        # out of order on purpose: rows must come back in input order
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 2]}, {"index": 0, "embedding": [3, 4]}]})

    out = embed(["a", "b"], "query", http_client=_client(handler))
    assert seen["url"] == "https://api.nicrron.ai/v1/embeddings"
    assert seen["auth"] == "Bearer n-key"
    assert seen["model"] == "voyage/voyage-finance-2" and seen["input_type"] == "query"
    np.testing.assert_allclose(out, [[0.6, 0.8], [0.0, 1.0]], rtol=1e-6)


def test_embed_batches_large_inputs():
    sizes = []

    def handler(request):
        n = len(json.loads(request.content)["input"])
        sizes.append(n)
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [1, 0]} for i in range(n)]})

    assert embed(["x"] * (BATCH_SIZE + 5), http_client=_client(handler)).shape == (BATCH_SIZE + 5, 2)
    assert sizes == [BATCH_SIZE, 5]


def test_embed_retries_a_rate_limit(monkeypatch):
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"error": "slow down"})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})

    assert embed(["a"], http_client=_client(handler)).shape == (1, 2)
    assert len(calls) == 2


def test_embed_errors_are_embedding_errors(monkeypatch):
    with pytest.raises(EmbeddingError, match="failed"):
        embed(["a"], http_client=_client(lambda r: httpx.Response(400, json={})))
    with pytest.raises(EmbeddingError, match="got 0"):
        embed(["a"], http_client=_client(lambda r: httpx.Response(200, json={"data": []})))
    monkeypatch.delenv("NICRRON_API_KEY")
    with pytest.raises(EmbeddingError, match="NICRRON_API_KEY"):
        embed(["a"])


def test_top_k_orders_by_similarity():
    matrix = np.array([[1, 0], [0, 1], [0.6, 0.8]], dtype=np.float32)
    assert [i for i, _ in top_k(np.array([0, 1], dtype=np.float32), matrix, 2)] == [1, 2]
    assert top_k(np.array([1, 0], dtype=np.float32), matrix, 10)[0][0] == 0  # k larger than rows is fine
