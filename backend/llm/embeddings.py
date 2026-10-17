"""Text embeddings through the Nicrron gateway (POST /v1/embeddings, OpenAI-compatible).

Embeddings don't answer questions; they place texts in a vector space where related texts sit
close together. This project uses them for retrieval only, in front of the Jev classifier:
- market search: a plain-English risk ("chip export controls china") against every open market;
- enrichment: a shortlist of the entities closest to a market, so Jev is asked once about
  ~30 likely candidates instead of twice about the whole map.

Default model: voyage/voyage-finance-2 (finance-tuned, 1024 dims, 32k context). Override with
NICRRON_EMBED_MODEL. Vectors come back L2-normalized, so cosine similarity is a dot product.
"""

import os

import httpx
import numpy as np

from backend.llm.client import NICRRON_BASE_URL, LlmError, post_with_retry

EMBED_URL = f"{NICRRON_BASE_URL}/embeddings"
DEFAULT_EMBED_MODEL = "voyage/voyage-finance-2"
BATCH_SIZE = 128         # inputs per request
MAX_INPUT_CHARS = 4000   # a market's text is a few hundred chars; this only guards against outliers


class EmbeddingError(LlmError):
    """The embeddings call failed or came back in an unexpected shape."""


def embed_model() -> str:
    return os.environ.get("NICRRON_EMBED_MODEL") or DEFAULT_EMBED_MODEL


def embed(texts: list[str], input_type: str = "document", model: str | None = None,
          http_client: httpx.Client | None = None) -> np.ndarray:
    """One normalized vector per text, shape (len(texts), dims), float32.

    `input_type` is "document" for what gets searched and "query" for what searches it
    (Voyage tunes the two sides differently). Raises EmbeddingError.
    """
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    key = os.environ.get("NICRRON_API_KEY")
    if not key:
        raise EmbeddingError("NICRRON_API_KEY is not set in environment")
    model = model or embed_model()
    client = http_client or httpx.Client()
    rows: list[list[float]] = []
    try:
        for start in range(0, len(texts), BATCH_SIZE):
            batch = [(t or " ")[:MAX_INPUT_CHARS] for t in texts[start:start + BATCH_SIZE]]
            try:
                response = post_with_retry(client, EMBED_URL, headers={"Authorization": f"Bearer {key}"},
                                           json={"model": model, "input": batch, "input_type": input_type},
                                           timeout=60)
                response.raise_for_status()
                data = sorted(response.json()["data"], key=lambda d: d.get("index", 0))
                vectors = [d["embedding"] for d in data]
            except httpx.HTTPError as exc:
                raise EmbeddingError(f"Embedding request failed: {exc}") from exc
            except (KeyError, TypeError, ValueError) as exc:
                raise EmbeddingError(f"Unexpected embedding response shape: {exc}") from exc
            if len(vectors) != len(batch):
                raise EmbeddingError(f"Asked for {len(batch)} embeddings, got {len(vectors)}")
            rows.extend(vectors)
    finally:
        if http_client is None:
            client.close()
    return normalize(np.asarray(rows, dtype=np.float32))


def normalize(matrix: np.ndarray) -> np.ndarray:
    """Rows scaled to unit length (a zero row stays zero)."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)


def top_k(query: np.ndarray, matrix: np.ndarray, k: int) -> list[tuple[int, float]]:
    """(row index, cosine similarity) of the `k` rows of `matrix` closest to `query`, best first.
    Both sides must already be normalized."""
    if matrix.size == 0 or k <= 0:
        return []
    scores = matrix @ query
    k = min(k, len(scores))
    best = np.argpartition(-scores, k - 1)[:k]
    best = best[np.argsort(-scores[best])]
    return [(int(i), float(scores[i])) for i in best]
