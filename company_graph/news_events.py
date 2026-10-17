"""News events (F6): fetch recent news for a graph's companies, type each story with Jev, and save
the typed stories into `graph_events` with source "news".

Flow, for one search:

1. `fetch_news_events(symbols)` makes at most one NewsAPI request (`poll_news`) for every company
   whose stored result is older than GRAPH_NEWS_TTL_HOURS. Company names are joined by OR in
   `boolean_terms`. NewsAPI caps `q` at 500 characters (https://newsapi.org/docs/endpoints/everything,
   "Max length: 500 chars"); a second request is made only when the names do not fit in one.
   Requests stop once GRAPH_NEWS_DAILY_BUDGET have been made that UTC day.
2. Articles are deduplicated by URL and by normalized title (syndicated copies of one story).
3. `classify_event(item)` asks Jev for one of `schemas.NEWS_EVENT_TYPES`, or `none` (opinion,
   roundups, anything else), which maps to None. With GRAPH_LLM_PROVIDER=anthropic it asks Claude
   through `llm.complete` instead, with the same labels and descriptions, so F6 runs without an
   OpenRouter key.
4. `save_news_events(session, events)` adds one `graph_events` row per (company, story), skipping
   rows that already exist. It does not commit.

`refresh_news_events(session, symbols)` runs all of it.

The per-company news result, the Jev label per URL and the daily request count have no table: they
live in one small JSON file, .cache/company_graph/news_cache.json (`NewsStore`). Losing it costs at
most a few repeated requests.

poll_news, Jev and llm.complete are synchronous, so they run through asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

from backend.classification import ChoiceSpec, JevError, classify
from backend.entities import EntityAlias
from backend.ingestion.news_api import ContentItem, NewsApiError, NewsQueryFilters, poll_news
from company_graph import config as config_mod
from company_graph import llm
from company_graph.llm import LlmError
from company_graph.schemas import NEWS_EVENT_TYPES

logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).resolve().parents[1] / ".cache" / "company_graph" / "news_cache.json"
NEWSAPI_MAX_QUERY_CHARS = 500  # NewsAPI's documented limit on `q`
NONE_LABEL = "none"

# Jev labels: one per news event type, plus "none". Descriptions stay under Jev's 255-character cap.
EVENT_LABELS: dict[str, str] = {
    "product_launch": "The company announces, launches, unveils or starts shipping a new product or service.",
    "contract": "The company wins, signs, loses or ends a contract, supply deal, order or partnership with a named party.",
    "earnings_surprise": "The company reports quarterly or annual results or guidance that beat or missed expectations.",
    "recall": "The company recalls a product, or a regulator orders or investigates a recall or safety defect.",
    "acquisition": "The company agrees to buy, merge with, or be bought by another company, or sells a business unit.",
    NONE_LABEL: "Anything else: opinion or analysis pieces, market roundups, stock-price moves, listicles, "
                "or news that is not one of the other events.",
}
EVENT_QUESTION = "Which company event does this news article report as fact?"

assert set(EVENT_LABELS) == set(NEWS_EVENT_TYPES) | {NONE_LABEL}, "EVENT_LABELS out of step with schemas"


@dataclass
class NewsEvent:
    """One typed story. `symbols` are the graph companies it mentions; it is saved once per symbol."""

    symbols: list[str]
    event_type: str
    title: str
    url: str
    occurred_at: datetime


# --- storage for the result cache and the daily budget -----------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NewsStore:
    """The JSON file holding per-company news results, Jev labels by URL, and the daily request count.

    Shape: {"budget": {"date": "YYYY-MM-DD", "count": n},
            "companies": {SYMBOL: {"fetched_at": iso, "items": [ContentItem json, ...]}},
            "labels": {url: {"label": str, "at": iso}}}
    Writes go through a temporary file and a rename, so a crash never leaves half a file.
    """

    def __init__(self, path: Path | str = CACHE_FILE, item_model: type[BaseModel] = ContentItem):
        self.path = Path(path)
        self.item_model = item_model  # the social step (F12) stores tweets with the same shape
        self.data: dict[str, Any] = {"budget": {}, "companies": {}, "labels": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text())
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except (OSError, ValueError):
                logger.warning("unreadable news cache at %s; starting empty", self.path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        tmp.replace(self.path)

    # budget
    def requests_today(self, now: datetime) -> int:
        b = self.data.get("budget") or {}
        return int(b.get("count", 0)) if b.get("date") == now.date().isoformat() else 0

    def record_request(self, now: datetime) -> None:
        self.data["budget"] = {"date": now.date().isoformat(), "count": self.requests_today(now) + 1}

    # per-company results
    def cached(self, symbol: str) -> tuple[datetime, list[BaseModel]] | None:
        entry = (self.data.get("companies") or {}).get(symbol)
        if not entry:
            return None
        try:
            fetched = datetime.fromisoformat(entry["fetched_at"])
            items = [self.item_model.model_validate(i) for i in entry.get("items", [])]
        except (KeyError, ValueError, TypeError):
            return None
        return fetched, items

    def put(self, symbol: str, items: list[ContentItem], now: datetime) -> None:
        self.data.setdefault("companies", {})[symbol] = {
            "fetched_at": now.isoformat(),
            "items": [i.model_dump(mode="json") for i in items],
        }

    # Jev labels
    def label(self, url: str) -> tuple[bool, str | None]:
        entry = (self.data.get("labels") or {}).get(url)
        if entry is None:
            return False, None
        return True, entry.get("label")

    def put_label(self, url: str, label: str | None, now: datetime) -> None:
        self.data.setdefault("labels", {})[url] = {"label": label, "at": now.isoformat()}

    def prune(self, now: datetime, keep_s: float) -> None:
        """Drop labels and company results older than `keep_s` so the file stays small."""
        cutoff = now - timedelta(seconds=keep_s)
        for key in ("labels", "companies"):
            section = self.data.get(key) or {}
            stamp = "at" if key == "labels" else "fetched_at"
            for k in [k for k, v in section.items() if _parse_time(v.get(stamp)) < cutoff]:
                del section[k]


def _parse_time(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


# One lock per process: two searches at once must not both spend a request or both rewrite the file.
_STORE_LOCK = asyncio.Lock()


# --- query building ------------------------------------------------------------------------------

def search_terms(aliases: Sequence[EntityAlias]) -> list[str]:
    """One search name per company: its short name (the last alias), else the symbol itself
    (non-US companies use their name as the symbol)."""
    terms: list[str] = []
    for a in aliases:
        name = (a.aliases[-1] if a.aliases else a.symbol).replace('"', "").strip()
        if name and name not in terms:
            terms.append(name)
    return terms


def build_queries(terms: Sequence[str], max_chars: int = NEWSAPI_MAX_QUERY_CHARS) -> list[str]:
    """Join quoted names with OR, starting a new query only when the next name would pass `max_chars`.

    Normally this is one query: 13 company names fit well inside 500 characters.
    """
    queries: list[str] = []
    current = ""
    for term in terms:
        quoted = f'"{term}"'
        if len(quoted) > max_chars:
            logger.warning("skipping a company name longer than NewsAPI's query limit: %s", term[:40])
            continue
        candidate = f"{current} OR {quoted}" if current else quoted
        if len(candidate) <= max_chars:
            current = candidate
        else:
            queries.append(current)
            current = quoted
    if current:
        queries.append(current)
    return queries


# --- dedup ---------------------------------------------------------------------------------------

_TRAILING_OUTLET = re.compile(r"\s+[-|–—]\s+[^-|–—]{1,60}$")  # " - Reuters", " | CNBC"


def normalize_title(title: str) -> str:
    s = _TRAILING_OUTLET.sub("", (title or "").strip())
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return " ".join(s.split())


def normalize_url(url: str) -> str:
    parts = urlsplit((url or "").strip())
    host = parts.netloc.lower().removeprefix("www.")
    return urlunsplit(("", host, parts.path.rstrip("/"), "", ""))


def dedupe(items: Iterable[ContentItem]) -> list[ContentItem]:
    """Collapse items with the same URL or the same normalized title, earliest first.

    The kept item gets the union of the duplicates' company tags.
    """
    kept: list[ContentItem] = []
    by_key: dict[str, ContentItem] = {}
    for item in sorted(items, key=lambda i: i.published_at):
        keys = [f"u:{normalize_url(item.url)}"]
        title_key = normalize_title(item.title)
        if title_key:
            keys.append(f"t:{title_key}")
        first = next((by_key[k] for k in keys if k in by_key), None)
        if first is None:
            first = item.model_copy(deep=True)
            kept.append(first)
        else:
            first.entities = list(dict.fromkeys([*first.entities, *item.entities]))
        for k in keys:
            by_key.setdefault(k, first)
    return kept


# --- tagging -------------------------------------------------------------------------------------

def confirm_tags(item: ContentItem, entity_list: Sequence[EntityAlias]) -> list[str]:
    """Re-check the matcher's tags so short tickers do not tag every article.

    EntityMatcher matches the symbol case-insensitively, so ticker "A" or "ON" matches the ordinary
    words "a" and "on". A company with aliases keeps its tag only when an alias matches (any case)
    or the symbol appears in capitals. A company without aliases (its symbol is its name) keeps
    the matcher's answer.
    """
    haystack = f"{item.title} {item.text or ''}"
    by_symbol = {a.symbol: a for a in entity_list}
    kept: list[str] = []
    for sym in item.entities:
        a = by_symbol.get(sym)
        if a is None or not a.aliases:
            kept.append(sym)
            continue
        if re.search(rf"\b{re.escape(sym)}\b", haystack) or any(
            re.search(rf"\b{re.escape(alias)}\b", haystack, re.IGNORECASE) for alias in a.aliases
        ):
            kept.append(sym)
    return kept


# --- fetching ------------------------------------------------------------------------------------

def _default_aliases(symbols: Sequence[str]) -> list[EntityAlias]:
    from company_graph.companies import aliases_for

    return aliases_for(symbols)


def _default_gateway(cfg: config_mod.Config):
    from backend.ingestion.news_api import NewsApiGateway

    if not cfg.newsapi_key:
        raise RuntimeError("NEWSAPI_KEY is not set")
    return NewsApiGateway(api_key=cfg.newsapi_key)


async def fetch_news_events(
    symbols: Sequence[str],
    *,
    cfg: config_mod.Config | None = None,
    store: NewsStore | None = None,
    gateway: Any = None,
    poll: Callable[..., list[ContentItem]] = poll_news,
    aliases: Callable[[Sequence[str]], list[EntityAlias]] = _default_aliases,
    now: datetime | None = None,
) -> list[ContentItem]:
    """Recent news about `symbols`, tagged with the symbols each article mentions, deduplicated.

    Companies with a stored result younger than GRAPH_NEWS_TTL_HOURS are served from it. The rest
    share one NewsAPI request (two only if their names pass 500 characters), unless the daily
    budget is spent or NewsAPI fails, in which case the stale stored result (or nothing) is used.
    Fake mode calls nothing and returns [].
    """
    cfg = cfg or config_mod.load()
    if cfg.fake or not symbols:
        return []
    now = now or _utcnow()
    symbols = list(dict.fromkeys(symbols))
    store = store or NewsStore()
    window_start = now - timedelta(seconds=cfg.event_window_s)

    async with _STORE_LOCK:
        items: list[ContentItem] = []
        stale: list[str] = []
        for sym in symbols:
            hit = store.cached(sym)
            if hit and (now - hit[0]).total_seconds() < cfg.news_ttl_s:
                items.extend(hit[1])
            else:
                stale.append(sym)

        if stale:
            entity_list = aliases(stale)
            queries = build_queries(search_terms(entity_list))
            fetched: list[ContentItem] = []
            ok = bool(queries)
            for q in queries:
                if store.requests_today(now) >= cfg.graph_news_daily_budget:
                    logger.warning("GRAPH_NEWS_DAILY_BUDGET (%s) spent; using stored news", cfg.graph_news_daily_budget)
                    ok = False
                    break
                filters = NewsQueryFilters(boolean_terms=q, from_time=window_start)
                store.record_request(now)
                store.save()  # count the request even if the process dies mid-call
                try:
                    gw = gateway if gateway is not None else _default_gateway(cfg)
                    batch = await asyncio.to_thread(poll, filters, gw, entity_list)
                    fetched.extend(i.model_copy(update={"entities": confirm_tags(i, entity_list)}) for i in batch)
                except (NewsApiError, RuntimeError) as exc:
                    logger.warning("news fetch failed: %s; using stored news", exc)
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
            store.prune(now, max(cfg.event_window_s, cfg.news_ttl_s))
            store.save()

    wanted = set(symbols)
    recent: list[ContentItem] = []
    for item in items:
        tags = [s for s in item.entities if s in wanted]
        if not tags or item.published_at < window_start:
            continue
        recent.append(item.model_copy(update={"entities": tags}))
    return dedupe(recent)


# --- classification ------------------------------------------------------------------------------

EVENT_SPEC = ChoiceSpec(question=EVENT_QUESTION, labels=EVENT_LABELS)


MODEL_TEXT_CHARS = 4000  # article text sent to the model (NewsAPI's `content` is short anyway)

EVENT_SYSTEM = (
    "You label one news article with the company event it reports as fact. Answer with exactly one "
    "label from the list. Pick \"none\" for opinion or analysis pieces, market roundups, stock-price "
    "moves, listicles, rumours, and anything that is not one of the other events.\n\nLabels:\n"
    + "\n".join(f"- {k}: {v}" for k, v in EVENT_LABELS.items())
)


class EventLabel(BaseModel):
    """The model's answer when GRAPH_LLM_PROVIDER=anthropic: one of EVENT_LABELS."""

    label: Literal[tuple(EVENT_LABELS)]  # type: ignore[valid-type]


def event_prompt(item: ContentItem) -> str:
    text = (item.text or "").strip()[:MODEL_TEXT_CHARS]
    return f"{EVENT_QUESTION}\n\nTitle: {item.title}\n\nText: {text or '(none)'}"


def classify_event(item: ContentItem, classifier: Callable = classify, provider: str | None = None) -> str | None:
    """The event type for one article, or None for opinion pieces, roundups and anything else.

    `provider` defaults to GRAPH_LLM_PROVIDER. "anthropic": one `llm.complete` call (raises LlmError).
    Anything else: one Jev call through `classifier`, as before (raises JevError).
    Synchronous.
    """
    if provider is None:
        provider = config_mod.load().graph_llm_provider
    if provider == "anthropic":
        label = llm.complete(EVENT_SYSTEM, event_prompt(item), EventLabel).label
    else:
        label = classifier(item.title, item.text, EVENT_SPEC).label
    return label if label in NEWS_EVENT_TYPES else None


async def classify_items(
    items: Sequence[ContentItem],
    *,
    store: NewsStore | None = None,
    classifier: Callable = classify,
    now: datetime | None = None,
    max_concurrency: int = 8,
    provider: str | None = None,
) -> list[NewsEvent]:
    """Type each item through Jev or the model (on worker threads), reusing labels stored for its URL.

    `provider` defaults to GRAPH_LLM_PROVIDER (see classify_event). Items labelled `none` produce no
    event. An item whose call fails is skipped this time and not remembered, so the next search
    retries it.
    """
    now = now or _utcnow()
    if provider is None:
        provider = config_mod.load().graph_llm_provider
    sem = asyncio.Semaphore(max_concurrency)

    async def one(item: ContentItem) -> tuple[bool, str | None]:
        if store is not None:
            known, label = store.label(item.url)
            if known:
                return True, label
        async with sem:
            try:
                return False, await asyncio.to_thread(classify_event, item, classifier, provider)
            except (JevError, LlmError) as exc:
                logger.warning("event typing failed for %s: %s", item.url, exc)
                return True, "__error__"

    results = await asyncio.gather(*(one(i) for i in items))
    events: list[NewsEvent] = []
    for item, (known, label) in zip(items, results):
        if label == "__error__":
            continue
        if store is not None and not known:
            store.put_label(item.url, label, now)
        if label is not None:
            events.append(NewsEvent(symbols=list(item.entities), event_type=label, title=item.title,
                                    url=item.url, occurred_at=item.published_at))
    return events


# --- saving --------------------------------------------------------------------------------------

async def save_news_events(session, events: Sequence[NewsEvent], event_model=None, source: str = "news") -> list:
    """Add one `graph_events` row (source `source`, "news" by default) per (symbol, story) not already stored.

    `session` is a SQLAlchemy AsyncSession (or anything with async `execute`, `flush` and `add`).
    Flushes but does not commit. Returns the new rows.
    """
    from sqlalchemy import select

    if event_model is None:
        from backend.models.graph_event import GraphEvent as event_model  # noqa: N813

    pairs = {(sym, e.url): e for e in events for sym in e.symbols}
    if not pairs:
        return []
    urls = sorted({url for _, url in pairs})
    result = await session.execute(
        select(event_model.entity_symbol, event_model.url).where(event_model.url.in_(urls))
    )
    existing = {(row[0], row[1]) for row in result.all()}

    rows = []
    for (sym, url), e in pairs.items():
        if (sym, url) in existing:
            continue
        row = event_model(entity_symbol=sym, source=source, event_type=e.event_type, title=e.title,
                          url=url, occurred_at=e.occurred_at, alert_id=None)
        session.add(row)
        rows.append(row)
    if rows:
        await session.flush()
    return rows


async def refresh_news_events(
    session,
    symbols: Sequence[str],
    *,
    cfg: config_mod.Config | None = None,
    store: NewsStore | None = None,
    gateway: Any = None,
    poll: Callable[..., list[ContentItem]] = poll_news,
    aliases: Callable[[Sequence[str]], list[EntityAlias]] = _default_aliases,
    classifier: Callable = classify,
    now: datetime | None = None,
) -> list[NewsEvent]:
    """Fetch, type and save the news events for one graph's companies. Returns the typed events.

    Does not commit; the caller owns the transaction.
    """
    cfg = cfg or config_mod.load()
    if cfg.fake:
        return []
    store = store or NewsStore()
    now = now or _utcnow()
    items = await fetch_news_events(symbols, cfg=cfg, store=store, gateway=gateway, poll=poll,
                                    aliases=aliases, now=now)
    events = await classify_items(items, store=store, classifier=classifier, now=now,
                                  provider=cfg.graph_llm_provider)
    async with _STORE_LOCK:
        store.save()  # keep the new Jev labels
    await save_news_events(session, events)
    return events
