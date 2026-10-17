"""Relevance filtering: is this content actually about a given entity,
not just a passing/incidental mention? This is the per-entity relevance
filter called out in new_specs/jev-classifier.md and the older
specs/jev-classification.md ("per-entity relevance filtering for
sentiment") — a keyword/entity match alone pulls in noise (a sector
roundup that namedrops NVDA once, a PyPI package page that happens to
have "nvidia" in its name) that shouldn't count toward that entity's
signal.

Built entirely on the existing `boolean` mode — no new Jev primitive
needed, just a named convenience so callers don't hand-write the same
question every time. Runs the relevance checks concurrently (a thread
pool), since each is one independent network call and this is meant to
run ahead of a heavier step (e.g. sentiment classification) on whatever
survives the filter.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, TypeVar

from .classifier import classify
from .modes import BooleanSpec


class _TitledText(Protocol):
    title: str
    text: str | None


T = TypeVar("T", bound=_TitledText)


def is_relevant(title: str, text: str | None, entity: str, threshold: float = 0.5) -> bool:
    """Does this content actually concern `entity` itself, rather than
    just name-dropping it?

    The question's wording matters a lot here and was tuned empirically
    (see scripts/bench_jev_news.py's output) — an earlier phrasing ("is
    this specifically about X, not just alongside other subjects")
    scored ordinary multi-company financial news the same as it scored
    junk (both ~0.5, i.e. a coin flip), because plenty of real NVDA news
    legitimately discusses NVDA alongside AMD/Micron/market context. This
    phrasing instead names the actual noise categories a keyword match
    lets through (package listings, broken pages, unrelated reviews,
    incidental name-drops), which separates real signal (~0.7-0.8) from
    junk (~0.01-0.15) cleanly.
    """
    spec = BooleanSpec(
        question=(
            f"Is this a real news article or financial report that is substantively "
            f"about {entity} as a company, its stock, products, or business — as "
            f"opposed to: a software package listing or changelog, a broken/error "
            f"webpage, a product review of an unrelated company, or an article that "
            f"only incidentally name-drops {entity} in a list of other companies "
            f"or products?"
        )
    )
    result = classify(title, text, spec)
    return result.probability >= threshold


def filter_relevant(
    items: list[T], entity: str, threshold: float = 0.5, max_workers: int = 10
) -> tuple[list[T], list[T]]:
    """Split `items` (anything with .title/.text — a news or tweet
    ContentItem, or any duck-typed equivalent) into (relevant, dropped)
    for `entity`. Order within each list matches the input order.
    """
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        decisions = list(pool.map(lambda item: is_relevant(item.title, item.text, entity, threshold), items))

    relevant = [item for item, keep in zip(items, decisions) if keep]
    dropped = [item for item, keep in zip(items, decisions) if not keep]
    return relevant, dropped
