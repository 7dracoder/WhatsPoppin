"""Social events (F12): find recent X posts about a graph's companies, keep the ones Jev says state a
company event as fact, and save them into `graph_events` with source "social".

Flow, for one search (the same shape as F6's news_events):

1. `fetch_social_items(symbols)` makes at most one X recent-search request for every company whose
   stored result is older than GRAPH_SOCIAL_TTL_HOURS. Company names are joined by OR, with
   retweets, replies and non-English posts left out. X caps a query at 512 characters
   (https://docs.x.com/x-api/posts/search/integrate/build-a-query); a second request is made only
   when the names do not fit in one. Each request reads one page (up to 100 posts, X's "relevancy"
   order). Requests stop once GRAPH_SOCIAL_DAILY_BUDGET have been made that UTC day.
2. Each post is tagged with the companies it names: an alias in any case, a cashtag ($NVDA), or a
   ticker of two or more letters in capitals. Posts with fewer than GRAPH_SOCIAL_MIN_LIKES likes and
   copies of the same text are dropped, and so are stock lists (more than MAX_COMPANIES_PER_POST
   graph companies or MAX_CASHTAGS_PER_POST cashtags). At most MAX_POSTS_PER_RUN, most liked first, go on.
3. `classify_post(item, company)` asks Jev, once per company the post names, whether the post states
   one of `schemas.NEWS_EVENT_TYPES` as fact about that company, or is `none` (opinion, prediction,
   rumour, plan, joke, ad, stock list, price chatter, or someone else's event), which is ignored. A
   label with probability under MIN_PROBABILITY is ignored too. Jev is used whatever
   GRAPH_LLM_PROVIDER is.
4. The typed posts are saved with F6's `save_news_events(..., source="social")`.

`refresh_social_events(session, symbols)` runs all of it. Results, Jev labels by post URL and the
daily request count live in .cache/company_graph/social_cache.json (F6's `NewsStore`, holding
tweets instead of articles). The X gateway and Jev are synchronous, so they run through
asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from backend.classification import ChoiceSpec, JevError, classify
from backend.entities import EntityAlias
from backend.ingestion.twitter_lookup import ContentItem, TwitterApiError
from backend.ingestion.twitter_lookup.normalize import normalize_batch
from company_graph import config as config_mod
from company_graph.news_events import EVENT_LABELS, NONE_LABEL, NewsEvent, NewsStore, save_news_events
from company_graph.schemas import NEWS_EVENT_TYPES

logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).resolve().parents[1] / ".cache" / "company_graph" / "social_cache.json"
X_MAX_QUERY_CHARS = 512     # X's documented limit on a recent-search query
QUERY_FILTERS = "-is:retweet -is:reply lang:en"
PAGES_PER_REQUEST = 1       # one page of up to 100 posts; X bills per post read
SORT_ORDER = "relevancy"
X_SEARCH_DAYS = 7           # recent search covers the last 7 days only
MAX_POSTS_PER_RUN = 40      # most posts sent to Jev in one search
MIN_PROBABILITY = 0.9       # Jev's probability for the chosen event below this is treated as none
MAX_COMPANIES_PER_POST = 2  # posts naming more graph companies are stock lists, not news
MAX_CASHTAGS_PER_POST = 3   # same for posts with many cashtags
TITLE_CHARS = 300

POST_LABELS: dict[str, str] = {
    **{k: v for k, v in EVENT_LABELS.items() if k != NONE_LABEL},
    NONE_LABEL: "Anything else: opinions, predictions, rumours, plans, questions, jokes, ads, lists of stocks, "
                "stock-price chatter, or an event done by someone other than the named company.",
}


def post_spec(company: str) -> ChoiceSpec:
    """The Jev question for one post about one company. The company is named so a post that only
    mentions it (a stock list, someone else's launch) is answered "none"."""
    return ChoiceSpec(
        question=f"Does this social media post report, as something that has already happened, one of these "
                 f"events done by or to {company} itself?",
        labels=POST_LABELS,
    )

assert set(POST_LABELS) == set(NEWS_EVENT_TYPES) | {NONE_LABEL}, "POST_LABELS out of step with schemas"
assert all(len(v) <= 255 for v in POST_LABELS.values()), "Jev label descriptions are capped at 255 characters"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# One lock per process, separate from F6's: two searches must not both spend a request or both rewrite the file.
_STORE_LOCK = asyncio.Lock()


def social_store(path: Path | str = CACHE_FILE) -> NewsStore:
    return NewsStore(path, item_model=ContentItem)


# --- companies and queries -----------------------------------------------------------------------

def _default_aliases(symbols: Sequence[str]) -> list[EntityAlias]:
    """The directory's names for each company, plus the built-in seed aliases ("Nvidia", "Disney")."""
    from backend.entities import load_entities
    from company_graph.companies import aliases_for

    seed = {e.symbol: e.aliases for e in load_entities()}
    out = []
    for a in aliases_for(symbols):
        extra = [s for s in seed.get(a.symbol, []) if s not in a.aliases]
        out.append(EntityAlias(symbol=a.symbol, aliases=[*a.aliases, *extra]))
    return out


def search_name(alias: EntityAlias) -> str:
    """The name to search X for: the shortest alias (people write "Nvidia", not "NVIDIA Corp"), else the symbol."""
    names = [n.replace('"', "").strip() for n in alias.aliases if n.strip()]
    return min(names, key=len) if names else alias.symbol


def build_queries(names: Sequence[str], max_chars: int = X_MAX_QUERY_CHARS) -> list[str]:
    """`("A" OR "B" ...) -is:retweet -is:reply lang:en`, starting a new query only when the next name
    would pass `max_chars`."""
    room = max_chars - len(QUERY_FILTERS) - 3  # the brackets and the space before the filters
    queries: list[str] = []
    current = ""
    for name in dict.fromkeys(names):
        quoted = f'"{name}"'
        if len(quoted) > room:
            logger.warning("skipping a company name longer than X's query limit: %s", name[:40])
            continue
        candidate = f"{current} OR {quoted}" if current else quoted
        if len(candidate) <= room:
            current = candidate
        else:
            queries.append(current)
            current = quoted
    if current:
        queries.append(current)
    return [f"({q}) {QUERY_FILTERS}" for q in queries]


# --- tagging and filtering -----------------------------------------------------------------------

def tag_post(text: str, entity_list: Sequence[EntityAlias]) -> list[str]:
    """The companies a post names: an alias in any case, a cashtag ($NVDA, any case), or a ticker of
    two or more letters written in capitals. A one-letter ticker ("F", "A") needs its cashtag."""
    tags: list[str] = []
    for a in entity_list:
        sym = a.symbol
        hit = any(re.search(rf"\b{re.escape(n)}\b", text, re.IGNORECASE) for n in a.aliases)
        hit = hit or bool(re.search(rf"\${re.escape(sym)}\b", text, re.IGNORECASE))
        if not a.aliases:  # a non-US company stored under its name
            hit = hit or bool(re.search(rf"\b{re.escape(sym)}\b", text, re.IGNORECASE))
        elif len(sym) >= 2 and sym.isupper():
            hit = hit or bool(re.search(rf"(?<![$\w]){re.escape(sym)}\b", text))
        if hit:
            tags.append(sym)
    return tags


def _text_key(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text.lower())
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


_CASHTAG = re.compile(r"\$[A-Za-z]{1,6}\b")


def is_stock_list(item: ContentItem) -> bool:
    return len(item.entities) > MAX_COMPANIES_PER_POST or len(set(_CASHTAG.findall(item.text))) > MAX_CASHTAGS_PER_POST


def keep_posts(items: Sequence[ContentItem], min_likes: int, limit: int = MAX_POSTS_PER_RUN) -> list[ContentItem]:
    """Tagged posts with at least `min_likes` likes, one per text (copies dropped), most liked first.
    Stock lists (`is_stock_list`) are dropped."""
    seen: set[str] = set()
    kept: list[ContentItem] = []
    for item in sorted(items, key=lambda i: (-i.engagement.likes, i.published_at)):
        key = _text_key(item.text)
        if not item.entities or item.engagement.likes < min_likes or not key or key in seen or is_stock_list(item):
            continue
        seen.add(key)
        kept.append(item)
    return kept[:limit]


# --- fetching ------------------------------------------------------------------------------------

def _default_gateway(cfg: config_mod.Config):
    from backend.ingestion.twitter_lookup import TwitterApiGateway

    if not cfg.x_bearer_token:
        raise RuntimeError("X_BEARER_TOKEN is not set")
    return TwitterApiGateway(bearer_token=cfg.x_bearer_token)


def _search(gateway, query: str, start: datetime, end: datetime) -> list[ContentItem]:
    raw = gateway.fetch_recent_search(query, start, end, max_pages=PAGES_PER_REQUEST, sort_order=SORT_ORDER)
    return normalize_batch(raw)


async def fetch_social_items(
    symbols: Sequence[str],
    *,
    cfg: config_mod.Config | None = None,
    store: NewsStore | None = None,
    gateway: Any = None,
    search: Callable[..., list[ContentItem]] = _search,
    aliases: Callable[[Sequence[str]], list[EntityAlias]] = _default_aliases,
    now: datetime | None = None,
) -> list[ContentItem]:
    """Recent posts about `symbols`, tagged with the symbols each names, filtered by `keep_posts`.

    Companies with a stored result younger than GRAPH_SOCIAL_TTL_HOURS are served from it. The rest
    share one X request (two only if their names pass 512 characters), unless the daily budget is
    spent or X fails, in which case the stale stored result (or nothing) is used. Fake mode calls
    nothing and returns [].
    """
    cfg = cfg or config_mod.load()
    if cfg.fake or not symbols:
        return []
    now = now or _utcnow()
    symbols = list(dict.fromkeys(symbols))
    store = store or social_store()
    window_start = now - timedelta(seconds=cfg.event_window_s)
    # X rejects a start older than 7 days or an end less than 10 seconds ago.
    search_start = max(window_start, now - timedelta(days=X_SEARCH_DAYS) + timedelta(minutes=5))
    search_end = now - timedelta(seconds=30)

    async with _STORE_LOCK:
        items: list[ContentItem] = []
        stale: list[str] = []
        for sym in symbols:
            hit = store.cached(sym)
            if hit and (now - hit[0]).total_seconds() < cfg.social_ttl_s:
                items.extend(hit[1])
            else:
                stale.append(sym)

        if stale:
            entity_list = aliases(stale)
            queries = build_queries([search_name(a) for a in entity_list])
            fetched: list[ContentItem] = []
            ok = bool(queries)
            for q in queries:
                if store.requests_today(now) >= cfg.graph_social_daily_budget:
                    logger.warning("GRAPH_SOCIAL_DAILY_BUDGET (%s) spent; using stored posts", cfg.graph_social_daily_budget)
                    ok = False
                    break
                store.record_request(now)
                store.save()  # count the request even if the process dies mid-call
                try:
                    gw = gateway if gateway is not None else _default_gateway(cfg)
                    batch = await asyncio.to_thread(search, gw, q, search_start, search_end)
                    fetched.extend(i.model_copy(update={"entities": tag_post(i.text, entity_list)}) for i in batch)
                except (TwitterApiError, RuntimeError) as exc:
                    logger.warning("X search failed: %s; using stored posts", exc)
                    ok = False
                    break
            if ok:
                for sym in stale:
                    store.put(sym, [i for i in fetched if sym in i.entities], now)
                items.extend(fetched)
            else:
                items.extend(fetched)  # whatever arrived before the failure
                for sym in stale:
                    hit = store.cached(sym)
                    if hit:
                        items.extend(hit[1])
            store.prune(now, max(cfg.event_window_s, cfg.social_ttl_s))
            store.save()

    wanted = set(symbols)
    recent: list[ContentItem] = []
    for item in items:
        tags = [s for s in item.entities if s in wanted]
        if tags and item.published_at >= window_start:
            recent.append(item.model_copy(update={"entities": tags}))
    return keep_posts(recent, cfg.graph_social_min_likes)


# --- classification ------------------------------------------------------------------------------

def classify_post(item: ContentItem, company: str, classifier: Callable = classify) -> str | None:
    """The event type the post states as fact about `company` (a name), or None when Jev says it can
    be ignored. Raises JevError."""
    result = classifier(item.text, None, post_spec(company))
    if result.label not in NEWS_EVENT_TYPES:
        return None
    if (result.probability if result.probability is not None else 1.0) < MIN_PROBABILITY:
        return None
    return result.label


def post_title(item: ContentItem) -> str:
    text = " ".join(item.text.split())
    title = f"@{item.author} on X: {text}" if item.author else f"Post on X: {text}"
    return title if len(title) <= TITLE_CHARS else title[:TITLE_CHARS - 1].rstrip() + "…"


async def classify_posts(
    items: Sequence[ContentItem],
    *,
    names: dict[str, str] | None = None,
    store: NewsStore | None = None,
    classifier: Callable = classify,
    now: datetime | None = None,
    max_concurrency: int = 8,
) -> list[NewsEvent]:
    """Ask Jev about each (post, company it names) pair on worker threads, reusing stored labels.

    `names` maps a symbol to the company name put in the question (default: the symbol). Pairs Jev
    ignores produce no event. A pair whose call fails is skipped this time and not remembered, so
    the next search retries it.
    """
    now = now or _utcnow()
    names = names or {}
    sem = asyncio.Semaphore(max_concurrency)
    pairs = [(item, sym) for item in items for sym in item.entities]

    async def one(item: ContentItem, sym: str) -> tuple[bool, str | None]:
        if store is not None:
            known, label = store.label(f"{item.url}#{sym}")
            if known:
                return True, label
        async with sem:
            try:
                return False, await asyncio.to_thread(classify_post, item, names.get(sym, sym), classifier)
            except JevError as exc:
                logger.warning("Jev failed on %s: %s", item.url, exc)
                return True, "__error__"

    results = await asyncio.gather(*(one(item, sym) for item, sym in pairs))
    events: list[NewsEvent] = []
    for (item, sym), (known, label) in zip(pairs, results):
        if label == "__error__":
            continue
        if store is not None and not known:
            store.put_label(f"{item.url}#{sym}", label, now)
        if label is not None:
            events.append(NewsEvent(symbols=[sym], event_type=label, title=post_title(item),
                                    url=item.url, occurred_at=item.published_at))
    return events


# --- the run -------------------------------------------------------------------------------------

async def refresh_social_events(
    session,
    symbols: Sequence[str],
    *,
    cfg: config_mod.Config | None = None,
    store: NewsStore | None = None,
    gateway: Any = None,
    search: Callable[..., list[ContentItem]] = _search,
    aliases: Callable[[Sequence[str]], list[EntityAlias]] = _default_aliases,
    classifier: Callable = classify,
    now: datetime | None = None,
) -> list[NewsEvent]:
    """Fetch, type and save the X events for one graph's companies. Returns the typed events.

    Does not commit; the caller owns the transaction.
    """
    cfg = cfg or config_mod.load()
    if cfg.fake:
        return []
    store = store or social_store()
    now = now or _utcnow()
    items = await fetch_social_items(symbols, cfg=cfg, store=store, gateway=gateway, search=search,
                                     aliases=aliases, now=now)
    entity_list = aliases(list(dict.fromkeys(s for i in items for s in i.entities)))
    names = {a.symbol: search_name(a) for a in entity_list}
    events = await classify_posts(items, names=names, store=store, classifier=classifier, now=now)
    async with _STORE_LOCK:
        store.save()  # keep the new Jev labels
    await save_news_events(session, events, source="social")
    return events
