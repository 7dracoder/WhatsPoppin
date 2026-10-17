"""`/twitter` router: on-demand tweet lookups via ingestion/twitter_lookup,
plus `/classify`, which chains a lookup into backend/classification and
backend/llm. See new_specs/twitter-jev-classification.md.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.deps import get_twitter_gateway
from backend.classification import ChoiceSpec, ClassificationResult, JevError, classify
from backend.ingestion.twitter_lookup import (
    ContentItem,
    TwitterApiError,
    TwitterApiGateway,
    lookup_account,
    lookup_keyword,
)
from backend.llm import LlmError, complete_structured

router = APIRouter(prefix="/twitter", tags=["twitter"])

DEFAULT_LOOKBACK = timedelta(days=5)

# /classify: fetch as much history as twitter-lookup's own caps allow
# (see new_specs/twitter-jev-classification.md Functional Requirement 2) —
# an account lookup caps on tweet count (~3,200, handled by client.py's
# pagination), a keyword search caps on time range (~7 days), so these
# two defaults are deliberately different shapes of "as much as possible."
ACCOUNT_LOOKBACK = timedelta(days=365 * 10)
SEARCH_LOOKBACK = timedelta(days=7)
DEFAULT_UNRELATED_THRESHOLD = 0.6
DEFAULT_CLASSIFY_CONCURRENCY = 10

GENERIC_RELEVANCE_CATEGORIES = [
    "regulation or policy changes",
    "major competitors",
    "supply-chain or trade partners",
    "macroeconomic conditions",
    "trade restrictions or tariffs",
]


class IndirectRelevanceCategories(BaseModel):
    categories: list[str]


class ClassifiedTweet(ContentItem):
    """ContentItem's fields, flattened, plus the classification result —
    matches new_specs/twitter-jev-classification.md's documented shape.
    """

    classification: ClassificationResult


def _default_range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    end = end or datetime.now(timezone.utc)
    start = start or end - DEFAULT_LOOKBACK
    return start, end


def _clamp_search_start(start: datetime, end: datetime) -> datetime:
    """Floor a keyword search's start at X's own ~7-day recent-search window.
    X hard-rejects (400 "start_time must be on or after ...") any start_time
    older than that rather than silently ignoring it, so a caller picking an
    older time range (e.g. an old chart spike) would otherwise 400 the whole
    request instead of just searching what X actually has.
    """
    return max(start, end - SEARCH_LOOKBACK)


@router.get("/account/{handle}", response_model=list[ContentItem])
def get_account_tweets(
    handle: str,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    gateway: TwitterApiGateway = Depends(get_twitter_gateway),
) -> list[ContentItem]:
    start, end = _default_range(start, end)
    try:
        return lookup_account(handle, start, end, gateway, entities=[])
    except TwitterApiError as exc:
        raise HTTPException(status_code=502 if exc.retryable else 400, detail=exc.message) from exc


@router.get("/search", response_model=list[ContentItem])
def search_tweets(
    query: str,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    gateway: TwitterApiGateway = Depends(get_twitter_gateway),
) -> list[ContentItem]:
    start, end = _default_range(start, end)
    start = _clamp_search_start(start, end)
    try:
        return lookup_keyword(query, start, end, gateway, entities=[])
    except TwitterApiError as exc:
        raise HTTPException(status_code=502 if exc.retryable else 400, detail=exc.message) from exc


def _resolve_categories(entity: str, tags: list[str] | None) -> list[str]:
    """Tiered category source for the relevance question — caller-supplied
    tags, else Haiku-generated, else a generic default. See
    new_specs/twitter-jev-classification.md Functional Requirement 6.
    """
    if tags:
        return tags
    try:
        result = complete_structured(
            system_prompt=(
                "List 3-6 short phrases describing indirect ways a social media "
                "post could relate to the given company's stock (e.g. policy, "
                "competitors, supply chain, regulation). Respond with only the "
                "category phrases, no explanation."
            ),
            user_query=entity,
            response_model=IndirectRelevanceCategories,
        )
        if result.categories:
            return result.categories
    except LlmError:
        pass
    return GENERIC_RELEVANCE_CATEGORIES


def _build_spec(entity: str, categories: list[str]) -> ChoiceSpec:
    category_list = ", ".join(categories)
    return ChoiceSpec(
        question=(
            f"Does this post suggest something that could affect {entity}'s stock — "
            f"directly (mentions {entity} or its product/company name), or indirectly "
            f"via: {category_list}? If neither applies, answer unrelated."
        ),
        labels={
            "bullish": f"Suggests good news for {entity}",
            "bearish": f"Suggests bad news for {entity}",
            "neutral": f"Relates to {entity} but isn't clearly good or bad news",
            "unrelated": "Does not relate to this entity at all",
        },
    )


def _classify_item(item: ContentItem, spec: ChoiceSpec) -> ClassificationResult | None:
    """classify() with the title/text swap tweets need (ContentItem.title
    is always None) — see Functional Requirement 1. Returns None (drop
    silently) on a JevError for this one post, rather than failing the
    whole request over one bad call.
    """
    try:
        return classify(item.text, None, spec)
    except JevError:
        return None


@router.get("/classify", response_model=list[ClassifiedTweet])
def classify_tweets(
    entity: str,
    handle: str | None = Query(default=None),
    query: str | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    tags: str | None = Query(default=None, description="comma-separated, optional"),
    threshold: float = Query(default=DEFAULT_UNRELATED_THRESHOLD),
    concurrency: int = Query(default=DEFAULT_CLASSIFY_CONCURRENCY),
    gateway: TwitterApiGateway = Depends(get_twitter_gateway),
) -> list[ClassifiedTweet]:
    """Fetch an account's/keyword's history in [start, end] (defaulting to
    as much as twitter-lookup allows, same as before), classify every post
    against `entity` in one Jev call each, and return only the ones that
    correlate (see new_specs/twitter-jev-classification.md). Nothing
    persisted. Note: a keyword `query` search is capped by X's own
    recent-search endpoint to roughly the last 7 days no matter what
    `start` is given; an account `handle` lookup has no such limit (up to
    X's ~3,200-tweet cap).
    """
    if bool(handle) == bool(query):
        raise HTTPException(status_code=400, detail="Provide exactly one of handle or query")

    end = end or datetime.now(timezone.utc)
    try:
        if handle:
            items = lookup_account(handle, start or end - ACCOUNT_LOOKBACK, end, gateway, entities=[])
        else:
            query_start = _clamp_search_start(start or end - SEARCH_LOOKBACK, end)
            items = lookup_keyword(query, query_start, end, gateway, entities=[])
    except TwitterApiError as exc:
        raise HTTPException(status_code=502 if exc.retryable else 400, detail=exc.message) from exc

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    spec = _build_spec(entity, _resolve_categories(entity, tag_list))

    results: list[ClassifiedTweet] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for item, result in zip(items, pool.map(lambda item: _classify_item(item, spec), items)):
            if result is None:
                continue
            if (result.probabilities or {}).get("unrelated", 0.0) >= threshold:
                continue
            results.append(ClassifiedTweet(**item.model_dump(), classification=result))
    return results
