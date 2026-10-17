"""`/classification` router: classify a small batch of items from the UI."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.deps import get_sentiment_store
from backend.classification import BooleanSpec, ClassificationResult, ClassificationSpec, JevError, SentimentSpec, classify
from backend.classification.sentiment_cache import CachedSentiment, SentimentStore, content_key

router = APIRouter(prefix="/classification", tags=["classification"])
logger = logging.getLogger(__name__)

# Each item is one Jev (paid, ~seconds-long) call, so cap the batch to bound
# cost and request latency.
MAX_ITEMS = 25
CONCURRENCY = 10

ClassifyFn = Callable[[str, str | None, ClassificationSpec], ClassificationResult]


class ClassificationItem(BaseModel):
    title: str
    text: str | None = None


class ClassificationQuery(BaseModel):
    items: list[ClassificationItem]
    query: str | None = None
    mode: Literal["sentiment", "boolean"]


class ClassificationSummary(BaseModel):
    percentage: int  # 0-100
    n: int  # items successfully classified
    label: str


def get_classify_fn() -> ClassifyFn:
    # A dependency (not a direct call) so tests can override it with a fake.
    return classify


@router.post("/query", response_model=ClassificationSummary)
def classify_query(body: ClassificationQuery, classify_fn: ClassifyFn = Depends(get_classify_fn)) -> ClassificationSummary:
    if body.mode == "boolean" and not (body.query and body.query.strip()):
        raise HTTPException(status_code=400, detail="query is required for boolean mode")
    if not body.items:
        raise HTTPException(status_code=400, detail="items must not be empty")
    if len(body.items) > MAX_ITEMS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_ITEMS} items per request")

    spec: ClassificationSpec = SentimentSpec() if body.mode == "sentiment" else BooleanSpec(question=body.query)
    positive_label = "positive" if body.mode == "sentiment" else "yes"

    def run(item: ClassificationItem) -> ClassificationResult | None:
        try:
            return classify_fn(item.title, item.text, spec)
        except JevError:
            return None  # one bad call shouldn't sink the batch; all-failed is handled below

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        results = [r for r in pool.map(run, body.items) if r is not None]

    if not results:
        raise HTTPException(status_code=502, detail="Classification service unavailable (the Jev call failed for every item)")

    hits = sum(1 for r in results if r.label == positive_label)
    percentage = round(100 * hits / len(results))
    noun = "positive" if body.mode == "sentiment" else f"answered yes: {body.query.strip()}"
    return ClassificationSummary(percentage=percentage, n=len(results), label=f"{percentage}% {noun}")


class ItemSentiment(BaseModel):
    """Jev's sentiment for one item, in the order the items were sent."""

    label: Literal["positive", "negative", "neutral"] | None  # None = Jev failed for this item
    probability: float | None = None
    cached: bool = False  # answered from the cache, no Jev call made


class SentimentBatch(BaseModel):
    items: list[ClassificationItem]


def _sentiment_of(result: ClassificationResult) -> ItemSentiment:
    label = result.label if result.label in ("positive", "negative", "neutral") else None
    return ItemSentiment(label=label, probability=result.probability)


def _classify_sentiments(items: list[ClassificationItem], classify_fn: ClassifyFn) -> list[ItemSentiment]:
    spec = SentimentSpec()

    def run(item: ClassificationItem) -> ItemSentiment:
        try:
            return _sentiment_of(classify_fn(item.title, item.text, spec))
        except JevError:
            return ItemSentiment(label=None)  # one bad call shouldn't sink the batch

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        return list(pool.map(run, items))  # map keeps input order


@router.post("/sentiment", response_model=list[ItemSentiment])
async def classify_sentiment(
    body: SentimentBatch,
    classify_fn: ClassifyFn = Depends(get_classify_fn),
    store: SentimentStore | None = Depends(get_sentiment_store),
) -> list[ItemSentiment]:
    """Per-item sentiment, where /query collapses a batch into one percentage.

    The UI colors each tweet/article by its own label, so it needs the labels, not the share
    that was positive. Each distinct text is sent to Jev (a paid call) at most once: results
    are cached by content hash, repeats inside one batch are classified once, and failures are
    never cached. A cache that errors is treated as empty, never as a failed request.
    """
    if not body.items:
        raise HTTPException(status_code=400, detail="items must not be empty")
    if len(body.items) > MAX_ITEMS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_ITEMS} items per request")

    keys = [content_key(i.title, i.text) for i in body.items]
    cached: dict[str, CachedSentiment] = {}
    if store is not None:
        try:
            cached = await store.get_many(list(set(keys)))
        except Exception:
            logger.warning("Sentiment cache read failed; classifying everything", exc_info=True)

    # One Jev call per distinct uncached text, even if the batch repeats it.
    to_classify = {k: item for k, item in zip(keys, body.items) if k not in cached}
    fresh: dict[str, ItemSentiment] = {}
    if to_classify:
        results = await asyncio.to_thread(_classify_sentiments, list(to_classify.values()), classify_fn)
        fresh = dict(zip(to_classify.keys(), results))
        if all(r.label is None for r in results) and not cached:
            raise HTTPException(
                status_code=502, detail="Classification service unavailable (the Jev call failed for every item)"
            )

    if store is not None:
        successes = {k: CachedSentiment(r.label, r.probability) for k, r in fresh.items() if r.label is not None}
        try:
            await store.put_many(successes)
        except Exception:
            logger.warning("Sentiment cache write failed; results returned uncached", exc_info=True)

    return [
        ItemSentiment(label=cached[k].label, probability=cached[k].probability, cached=True)
        if k in cached
        else fresh[k]
        for k in keys
    ]
