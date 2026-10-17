"""Pair news (F13): recent news and X posts about two linked companies together, such as NVIDIA and
Nebius. Background reading for one link of a graph; it does not change links or highlights.

`pair_news(a, b)`, for two companies (each a `Company`, or a name for a company with no US ticker):

1. Returns the stored result for the pair when it is younger than GRAPH_PAIR_TTL_HOURS.
2. Otherwise makes one NewsAPI request, `"A" AND "B"` over the last NEWS_DAYS days, and one X
   recent search, `"A" "B" -is:retweet -is:reply lang:en` (both names required, last 7 days, one page).
   Each source has its own daily cap, GRAPH_PAIR_DAILY_BUDGET requests a day. A source that is
   capped, unconfigured or failing is listed in `failed` and the other one is still used.
3. Drops copies (same URL or same headline) and stock-list posts, keeps the MAX_CHECKED most relevant
   per source, and asks Jev (title plus the article's opening text) whether each item is about the two companies together (a deal, supply, partnership,
   competition, a dispute, or one event involving both) or only names both. Only "together" with
   probability MIN_PROBABILITY or more is kept. An item Jev fails on is left out.
4. Stores and returns the kept items, newest first.

Results and request counts live in .cache/company_graph/pair_cache.json (`PairStore`). NewsAPI, the
X gateway and Jev are synchronous, so they run through asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel

from backend.classification import ChoiceSpec, JevError, classify
from company_graph import config as config_mod
from company_graph.companies import Company

logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).resolve().parents[1] / ".cache" / "company_graph" / "pair_cache.json"
NEWS_DAYS = 30          # NewsAPI's free plan searches about a month back
X_DAYS = 7              # X recent search covers the last 7 days only
NEWS_PAGE_SIZE = 30
MAX_CHECKED = 15        # most items per source sent to Jev
MIN_PROBABILITY = 0.7
X_FILTERS = "-is:retweet -is:reply lang:en"
TOGETHER = "together"

Source = Literal["news", "x"]


class PairItem(BaseModel):
    source: Source
    title: str
    url: str
    published_at: datetime
    by: str | None = None  # the outlet for news, the @handle for X
    text: str | None = None  # the article's opening text (NewsAPI), shown to Jev with the title


class PairResult(BaseModel):
    items: list[PairItem]
    failed: list[str]  # sources that could not be searched this time: "news", "x"


def pair_spec(a: str, b: str) -> ChoiceSpec:
    return ChoiceSpec(
        question=f"Is this text about {a} and {b} together?",
        labels={
            TOGETHER: f"It reports something involving both {a} and {b}: a deal, supply, partnership, "
                      f"investment, competition between them, a dispute, or one event affecting both.",
            "separate": f"It names {a} and {b} only in a list of stocks, a market roundup, a comparison of "
                        f"share prices, or in unrelated sentences.",
        },
    )


# --- storage -------------------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def pair_key(a: str, b: str) -> str:
    return "|".join(sorted((a.upper(), b.upper())))


class PairStore:
    """{"budget": {source: {"date", "count"}}, "pairs": {key: {"fetched_at", "result"}}}, written atomically."""

    def __init__(self, path: Path | str = CACHE_FILE):
        self.path = Path(path)
        self.data: dict[str, Any] = {"budget": {}, "pairs": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text())
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except (OSError, ValueError):
                logger.warning("unreadable pair cache at %s; starting empty", self.path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        tmp.replace(self.path)

    def requests_today(self, source: str, now: datetime) -> int:
        b = (self.data.get("budget") or {}).get(source) or {}
        return int(b.get("count", 0)) if b.get("date") == now.date().isoformat() else 0

    def record_request(self, source: str, now: datetime) -> None:
        self.data.setdefault("budget", {})[source] = {"date": now.date().isoformat(),
                                                      "count": self.requests_today(source, now) + 1}

    def cached(self, key: str, now: datetime, ttl_s: float) -> PairResult | None:
        entry = (self.data.get("pairs") or {}).get(key)
        if not entry:
            return None
        try:
            fetched = datetime.fromisoformat(entry["fetched_at"])
            result = PairResult.model_validate(entry["result"])
        except (KeyError, ValueError, TypeError):
            return None
        return result if (now - fetched).total_seconds() < ttl_s else None

    def put(self, key: str, result: PairResult, now: datetime) -> None:
        pairs = self.data.setdefault("pairs", {})
        pairs[key] = {"fetched_at": now.isoformat(), "result": result.model_dump(mode="json")}
        cutoff = now - timedelta(days=2)
        for k in [k for k, v in pairs.items() if datetime.fromisoformat(v["fetched_at"]) < cutoff]:
            del pairs[k]


_LOCK = asyncio.Lock()  # one pair search at a time per process: requests and the file stay consistent


# --- names and queries ---------------------------------------------------------------------------

def _default_names(symbols: list[str]) -> dict[str, str]:
    from company_graph.social_events import _default_aliases, search_name

    return {a.symbol: search_name(a) for a in _default_aliases(symbols)}


def news_query(a: str, b: str) -> str:
    return f'"{a}" AND "{b}"'


def x_query(a: str, b: str) -> str:
    return f'"{a}" "{b}" {X_FILTERS}'


# --- fetching ------------------------------------------------------------------------------------

def _news_search(cfg: config_mod.Config, query: str, start: datetime) -> list[PairItem]:
    from backend.ingestion.news_api import NewsApiGateway, NewsQueryFilters, poll_news
    from backend.ingestion.news_api.models import SortBy

    if not cfg.newsapi_key:
        raise RuntimeError("NEWSAPI_KEY is not set")
    filters = NewsQueryFilters(boolean_terms=query, from_time=start, sort_by=SortBy.RELEVANCY,
                               page_size=NEWS_PAGE_SIZE)
    items = poll_news(filters, NewsApiGateway(api_key=cfg.newsapi_key), [])
    return [PairItem(source="news", title=i.title, url=i.url, published_at=i.published_at, by=i.author,
                     text=i.text) for i in items if i.title and i.url]


def _x_search(cfg: config_mod.Config, query: str, start: datetime, end: datetime) -> list[PairItem]:
    from backend.ingestion.twitter_lookup import TwitterApiGateway
    from backend.ingestion.twitter_lookup.normalize import normalize_batch
    from company_graph.social_events import _CASHTAG, PAGES_PER_REQUEST, SORT_ORDER, post_title

    if not cfg.x_bearer_token:
        raise RuntimeError("X_BEARER_TOKEN is not set")
    raw = TwitterApiGateway(bearer_token=cfg.x_bearer_token).fetch_recent_search(
        query, start, end, max_pages=PAGES_PER_REQUEST, sort_order=SORT_ORDER)
    out = []
    for i in normalize_batch(raw):
        if len(set(_CASHTAG.findall(i.text))) > 3:  # a stock list, not news about the pair
            continue
        out.append(PairItem(source="x", title=post_title(i), url=i.url, published_at=i.published_at,
                            by=f"@{i.author}" if i.author else None))
    return out


def dedupe(items: list[PairItem]) -> list[PairItem]:
    """Drop copies: the same URL, or the same headline from another outlet. The earliest copy is kept,
    and the result keeps the input order (NewsAPI and X return the most relevant first)."""
    from company_graph.news_events import normalize_title, normalize_url

    def keys(item: PairItem) -> set[str]:
        out = {f"u:{normalize_url(item.url)}"}
        if item.source == "news" and normalize_title(item.title):
            out.add(f"t:{normalize_title(item.title)}")
        return out

    seen: set[str] = set()
    kept: set[int] = set()
    for idx, item in sorted(enumerate(items), key=lambda p: p[1].published_at):
        k = keys(item)
        if k & seen:
            continue
        seen |= k
        kept.add(idx)
    return [item for idx, item in enumerate(items) if idx in kept]


# --- Jev -----------------------------------------------------------------------------------------

def is_together(item: PairItem, a: str, b: str, classifier: Callable = classify) -> bool:
    """Whether Jev says the item is about `a` and `b` together. Raises JevError."""
    result = classifier(item.title, item.text, pair_spec(a, b))
    probability = result.probability if result.probability is not None else 1.0
    return result.label == TOGETHER and probability >= MIN_PROBABILITY


async def keep_together(items: list[PairItem], a: str, b: str, classifier: Callable = classify,
                        max_concurrency: int = 8) -> list[PairItem]:
    sem = asyncio.Semaphore(max_concurrency)

    async def one(item: PairItem) -> bool:
        async with sem:
            try:
                return await asyncio.to_thread(is_together, item, a, b, classifier)
            except JevError as exc:
                logger.warning("Jev failed on %s: %s", item.url, exc)
                return False

    verdicts = await asyncio.gather(*(one(i) for i in items))
    return [i for i, ok in zip(items, verdicts) if ok]


# --- the search ----------------------------------------------------------------------------------

async def pair_news(
    a: Company | str,
    b: Company | str,
    *,
    cfg: config_mod.Config | None = None,
    store: PairStore | None = None,
    names: Callable[[list[str]], dict[str, str]] = _default_names,
    news_search: Callable = _news_search,
    x_search: Callable = _x_search,
    classifier: Callable = classify,
    now: datetime | None = None,
) -> PairResult:
    """Recent news and X posts about `a` and `b` together, newest first. See the module docstring.

    Fake mode calls nothing and returns no items.
    """
    cfg = cfg or config_mod.load()
    if cfg.fake:
        return PairResult(items=[], failed=[])
    now = now or _utcnow()
    sym_a = a.symbol if isinstance(a, Company) else a
    sym_b = b.symbol if isinstance(b, Company) else b
    key = pair_key(sym_a, sym_b)
    store = store or PairStore()

    async with _LOCK:
        hit = store.cached(key, now, cfg.pair_ttl_s)
        if hit is not None:
            return hit

        found = names([sym_a, sym_b])
        name_a, name_b = found.get(sym_a, sym_a), found.get(sym_b, sym_b)
        failed: list[str] = []
        fetched: list[PairItem] = []

        searches: list[tuple[str, Callable[[], list[PairItem]]]] = [
            ("news", lambda: news_search(cfg, news_query(name_a, name_b), now - timedelta(days=NEWS_DAYS))),
            ("x", lambda: x_search(cfg, x_query(name_a, name_b),
                                   now - timedelta(days=X_DAYS) + timedelta(minutes=5), now - timedelta(seconds=30))),
        ]
        for source, run in searches:
            if store.requests_today(source, now) >= cfg.graph_pair_daily_budget:
                logger.warning("GRAPH_PAIR_DAILY_BUDGET (%s) spent for %s", cfg.graph_pair_daily_budget, source)
                failed.append(source)
                continue
            store.record_request(source, now)
            store.save()  # count the request even if the process dies mid-call
            try:
                batch = await asyncio.to_thread(run)
            except Exception as exc:  # noqa: BLE001 - one source failing must not hide the other
                logger.warning("pair search on %s failed for %s: %s", source, key, exc)
                failed.append(source)
                continue
            fetched.extend(dedupe(batch)[:MAX_CHECKED])  # the most relevant first

        kept = await keep_together(dedupe(fetched), name_a, name_b, classifier)
        result = PairResult(items=sorted(kept, key=lambda i: i.published_at, reverse=True), failed=failed)
        if len(failed) < len(searches):  # never cache a result where every source failed
            store.put(key, result, now)
        store.save()
        return result
