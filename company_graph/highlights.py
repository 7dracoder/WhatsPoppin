"""F8. Highlight builder: which linked companies a recent event may affect, and in which direction.

`build_highlights(symbol, session=...)`, for the searched company S:

1. Reads S's links with `links.read_links` (never a plain query). No links: nothing to do.
2. Refreshes events for S and its linked companies: news (F6, `refresh_news_events`),
   prediction-market alerts (F7, `fetch_market_events`) and X posts (F12,
   `refresh_social_events`, left out when X_BEARER_TOKEN is unset). Each step is optional: no
   NEWSAPI_KEY, no `alerts` table, a NewsAPI, X or Jev outage or any other failure is logged and skipped. Each
   successful step is committed, a failed one rolled back, so one cannot undo the other.
3. Loads `graph_events` from the last GRAPH_EVENT_WINDOW_DAYS for S and its linked companies.
4. Asks the model (`llm.complete`, one call per event, on worker threads) which candidate
   companies the event involves and why, one factual sentence each. Events flow both ways along
   a link, one hop only:
     - an event about S: the candidates are S's linked companies;
     - an event about a linked company L: the only candidate is S (its partner on that link).
       Such a highlight targets S, so it shows on L's graph and on any graph where S is a node,
       not on S's own page (the page never shows S as a node). An event about L never reaches
       S's other links directly; that would be two hops.
   Only companies the model names from the candidate list are kept.
5. Direction comes from the target's role on the link, never from the model:
     supplier, customer, partner -> may_benefit;  competitor -> may_face_pressure;
     sector_peer -> may_face_pressure only when the model says the event is a direct competitive
     gain for the event's company over that peer (`competitive_gain`); otherwise no highlight.
   Sector peers come from SEC's industry list, not from a filing that states a relationship, so
   being in the same industry alone says nothing about which way an event cuts. A clear win
   (a contract or a launch in a market both sell into) is the one case where a direction is
   defensible. A company with several link types keeps its preferred one (read_links' order).
6. `price_change_pct`: the target's percent change from the last bar at or before the event to
   the latest bar, from `backend.ingestion.alpaca.fetch_price_series` (smallest ZoomTier whose
   range covers the event, then one tier larger if that range has no bar before the event).
   Null when the Alpaca keys are missing, the call fails, or the symbol has no US ticker. It is
   a past fact, never a forecast.
7. Saves `graph_highlights`, at most one per (event, target), each with the event's URL (the
   URL the event was fetched from). Rows without a source URL are dropped.
8. Remembers which (S, event) pairs the model has already judged, with the candidates it was
   shown, in .cache/company_graph/highlight_evals.json (`EvalStore`). The model is asked again
   about an event only if S has gained a candidate since. A JSON file, like F6's news cache,
   because `company_graph.db.create_tables` lists its tables explicitly (a new model would need
   its own creation path) and losing the file costs only repeated model calls: duplicates are
   still prevented by the (event, target) check against `graph_highlights`.

Like `links.build_links`, this function commits as it goes: give it its own session.
Model, Jev, news and Alpaca calls are synchronous and run through asyncio.to_thread.
Wording rule: reasons state facts about the event and the relationship; a reason that reads
like a price prediction or a trade suggestion is dropped (`is_factual`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.models.graph_event import GraphEvent
from backend.models.graph_highlight import GraphHighlight
from company_graph import config as config_mod
from company_graph import links as graph_links
from company_graph import llm
from company_graph.extract import reverse_type
from company_graph.llm import LlmError

logger = logging.getLogger(__name__)

__all__ = [
    "build_highlights", "HighlightRunResult", "Candidate", "Involvement", "EventInvolvement",
    "direction_for", "candidates_for", "event_prompt", "ask_model", "pick_tier", "percent_change",
    "price_change_pct", "alpaca_gateway_from_env", "EvalStore", "is_factual", "save_highlights",
]

EVAL_FILE = Path(__file__).resolve().parents[1] / ".cache" / "company_graph" / "highlight_evals.json"
MAX_EVENTS_PER_RUN = 40          # newest first; caps model calls for one search
MAX_PARALLEL_MODEL_CALLS = 4
MAX_REASON_CHARS = 300

DIRECTION_BY_TYPE = {
    "supplier": "may_benefit",
    "customer": "may_benefit",
    "partner": "may_benefit",
    "competitor": "may_face_pressure",
}
# sector_peer: may_face_pressure only with competitive_gain (see the module docstring).

ROLE_TEXT = {
    "supplier": "supplies {s}",
    "customer": "is a customer of {s}",
    "partner": "is a partner of {s}",
    "competitor": "competes with {s}",
    "sector_peer": "is in the same industry as {s} (no stated business relationship)",
}

# Reasons that predict a price or suggest a trade are dropped.
_NOT_FACTUAL = re.compile(
    r"\b(buy|sell|short|invest(?:ors?)? should|price target|upside|downside|"
    r"(?:stock|shares?|price)s? (?:will|could|may|might|should|is likely to|are likely to) "
    r"(?:rise|fall|drop|climb|jump|surge|gain|decline|go up|go down|rally|tumble))\b",
    re.IGNORECASE,
)


# --- results and model shapes ---------------------------------------------------------

@dataclass
class HighlightRunResult:
    symbol: str
    linked: int = 0
    events: int = 0                 # events in the window for S and its links
    evaluated: int = 0              # events sent to the model this run
    reused: int = 0                 # events skipped because they were judged before
    saved: int = 0                  # highlights inserted
    steps_failed: list[str] = field(default_factory=list)  # 'news', 'market', 'social' (logged and skipped)
    model_errors: int = 0


@dataclass(frozen=True)
class Candidate:
    """A company an event may involve. `role` is its role on the link, relative to the event's company."""
    symbol: str
    name: str
    role: str
    summary: str


class Involvement(BaseModel):
    symbol: str = Field(description="The candidate's symbol, exactly as listed.")
    reason: str = Field(description="One factual sentence: how the event involves this company through "
                                    "the stated relationship. No price predictions, no advice.")
    competitive_gain: bool = Field(default=False, description=(
        "Only for same-industry companies: true only when the event is a direct competitive gain for "
        "the event's company over this company (for example a contract or launch in a market both sell into)."))


class EventInvolvement(BaseModel):
    involved: list[Involvement] = Field(default_factory=list)


SYSTEM = (
    "You read one recent company event and a list of companies linked to the event's company. "
    "List only the linked companies that this specific event plausibly involves through the stated "
    "relationship (for example, a launch that uses a supplier's parts, a contract with a customer, a "
    "competitor whose product competes with the one launched). Leave out companies the event does not "
    "touch; an empty list is a fine answer. For each, write one short factual sentence naming the event "
    "and the relationship. Never predict a stock price, never say a price will move, and never suggest "
    "buying or selling anything. Use only the symbols given."
)


# --- pure helpers ---------------------------------------------------------------------

def direction_for(role: str, competitive_gain: bool = False) -> str | None:
    """The highlight direction for a target with this role, or None (no highlight)."""
    if role == "sector_peer":
        return "may_face_pressure" if competitive_gain else None
    return DIRECTION_BY_TYPE.get(role)


def candidates_for(event_symbol: str, symbol: str, symbol_name: str, stored: Sequence) -> list[Candidate]:
    """Who an event may involve, one hop along S's links (`stored` is read_links output for S)."""
    first: dict[str, Any] = {}
    for link in stored:  # read_links order: each company's preferred type first
        first.setdefault(link.symbol, link)
    if event_symbol == symbol:
        return [Candidate(l.symbol, l.name or l.symbol, l.type, l.summary or "") for l in first.values()]
    link = first.get(event_symbol)
    if link is None:
        return []
    # S's role for the event's company is the link seen from the other side.
    return [Candidate(symbol, symbol_name, reverse_type(link.type), link.summary or "")]


def event_prompt(event, event_company: str, candidates: Sequence[Candidate]) -> str:
    when = _utc(event.occurred_at).strftime("%Y-%m-%d")
    kind = event.event_type.replace("_", " ")
    lines = [f"Event about {event_company} ({event.entity_symbol}), {when}, type: {kind}.",
             f"Headline: {event.title}", "", "Linked companies:"]
    for c in candidates:
        role = ROLE_TEXT.get(c.role, c.role).format(s=event_company)
        detail = f" Filing note: {c.summary}" if c.summary else ""
        lines.append(f"- {c.symbol} ({c.name}): {role}.{detail}")
    return "\n".join(lines)


# The model sometimes lists a company only to say the event does not involve it.
_NOT_INVOLVED = re.compile(r"\b(unrelated|not related|does not (?:directly )?(?:involve|affect|touch)|no (?:direct )?(?:link|connection))\b",
                           re.IGNORECASE)


def is_factual(reason: str) -> bool:
    return bool(reason.strip()) and not _NOT_FACTUAL.search(reason) and not _NOT_INVOLVED.search(reason)


def _clean_reason(reason: str) -> str:
    text = " ".join((reason or "").split())
    return text if len(text) <= MAX_REASON_CHARS else text[:MAX_REASON_CHARS - 1].rstrip() + "…"


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


# --- the model -----------------------------------------------------------------------

def ask_model(event, event_company: str, candidates: Sequence[Candidate],
              complete: Callable | None = None) -> EventInvolvement:
    """One model call for one event. Synchronous; raises LlmError."""
    complete = complete or llm.complete
    return complete(SYSTEM, event_prompt(event, event_company, candidates), EventInvolvement)


# --- prices --------------------------------------------------------------------------

_US_TICKER = re.compile(r"^[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z0-9]{1,2})?$")


def pick_tier(event_time: datetime, now: datetime):
    """The smallest ZoomTier whose range (tiers.tier_range) covers `event_time`, and the larger ones after it."""
    from backend.ingestion.alpaca import ZoomTier, tier_range

    order = [ZoomTier.RECENT, ZoomTier.DAILY, ZoomTier.WEEKLY, ZoomTier.MONTHLY, ZoomTier.ALL_TIME]
    t = _utc(event_time)
    for i, tier in enumerate(order):
        start, _ = tier_range(tier, now)
        if start <= t:
            return order[i:]
    return []


def percent_change(points: Sequence, event_time: datetime) -> float | None:
    """Percent change from the last point at or before `event_time` to the last point. Pure."""
    t = _utc(event_time)
    pts = sorted(points, key=lambda p: _utc(p.timestamp))
    before = [p for p in pts if _utc(p.timestamp) <= t]
    if not before or not pts:
        return None
    base, latest = before[-1].price_or_odds, pts[-1].price_or_odds
    if not base:
        return None
    return round((latest - base) / base * 100, 2)


def alpaca_gateway_from_env():
    """An AlpacaApiGateway from ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY, or None when either is missing."""
    key_id, secret = os.environ.get("ALPACA_API_KEY_ID", "").strip(), os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not (key_id and secret):
        return None
    from backend.ingestion.alpaca import AlpacaApiGateway

    return AlpacaApiGateway(key_id, secret)


async def price_change_pct(target: str, event_time: datetime, now: datetime, gateway,
                           fetch: Callable | None = None, cache: dict | None = None,
                           directory=None) -> float | None:
    """The target's percent change since just before the event, or None on any problem.

    A symbol that is not in SEC's list (`directory`, when given) or does not look like a ticker is
    a company with no US listing (its symbol is its name): None.
    """
    if gateway is None or not _US_TICKER.match(target):
        return None
    if directory is not None and directory.get(target) is None:
        return None
    if fetch is None:
        from backend.ingestion.alpaca import fetch_price_series as fetch
    ticker = target.replace("-", ".")  # SEC writes BRK-B, Alpaca BRK.B
    for tier in pick_tier(event_time, now)[:2]:
        key = (ticker, tier)
        try:
            if cache is not None and key in cache:
                points = cache[key]
            else:
                points = await asyncio.to_thread(fetch, ticker, tier, gateway, now)
                if cache is not None:
                    cache[key] = points
        except Exception as exc:  # noqa: BLE001 - any Alpaca or network failure leaves the value null
            logger.info("price for %s unavailable: %s", ticker, exc)
            if cache is not None:
                cache[key] = None
            return None
        if points is None:
            return None
        pct = percent_change(points, event_time)
        if pct is not None:
            return pct
    return None


# --- remembered evaluations ----------------------------------------------------------

class EvalStore:
    """Which events the model has judged for which searched company, in a small JSON file.

    Shape: {"evals": {"S|EVENT_SYMBOL|URL": {"at": iso, "candidates": [symbols]}}}
    Writes go through a temporary file and a rename.
    """

    def __init__(self, path: Path | str = EVAL_FILE):
        self.path = Path(path)
        self.data: dict[str, Any] = {"evals": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text())
                if isinstance(loaded, dict) and isinstance(loaded.get("evals"), dict):
                    self.data = loaded
            except (OSError, ValueError):
                logger.warning("unreadable highlight evaluations at %s; starting empty", self.path)

    @staticmethod
    def key(symbol: str, event_symbol: str, url: str) -> str:
        return f"{symbol}|{event_symbol}|{url}"

    def judged(self, symbol: str, event_symbol: str, url: str, candidates: Sequence[str]) -> bool:
        """True when this event was judged for `symbol` with at least these candidates."""
        entry = self.data["evals"].get(self.key(symbol, event_symbol, url))
        return entry is not None and set(candidates) <= set(entry.get("candidates", []))

    def record(self, symbol: str, event_symbol: str, url: str, candidates: Sequence[str], now: datetime) -> None:
        k = self.key(symbol, event_symbol, url)
        old = set((self.data["evals"].get(k) or {}).get("candidates", []))
        self.data["evals"][k] = {"at": now.isoformat(), "candidates": sorted(old | set(candidates))}

    def prune(self, now: datetime, keep_s: float) -> None:
        cutoff = now - timedelta(seconds=keep_s)
        evals = self.data["evals"]
        for k in [k for k, v in evals.items() if _parse_time(v.get("at")) < cutoff]:
            del evals[k]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        tmp.replace(self.path)


def _parse_time(value: Any) -> datetime:
    try:
        return _utc(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


_EVAL_LOCK = asyncio.Lock()


# --- saving --------------------------------------------------------------------------

@dataclass(frozen=True)
class NewHighlight:
    event_id: int
    source_symbol: str
    target_symbol: str
    direction: str
    reason: str
    source_url: str | None
    event_time: datetime
    price_change_pct: float | None = None


async def save_highlights(session, highlights: Sequence[NewHighlight]) -> list[GraphHighlight]:
    """Insert highlights not already stored for the same (event, target). Drops any without a
    source URL. Flushes, does not commit. Returns the new rows."""
    keep = [h for h in highlights if h.source_url and h.source_url.strip()]
    if not keep:
        return []
    event_ids = sorted({h.event_id for h in keep})
    existing = {(r[0], r[1]) for r in (await session.execute(
        select(GraphHighlight.event_id, GraphHighlight.target_symbol).where(GraphHighlight.event_id.in_(event_ids))
    )).all()}
    rows = []
    for h in keep:
        k = (h.event_id, h.target_symbol)
        if k in existing:
            continue
        existing.add(k)
        row = GraphHighlight(event_id=h.event_id, source_symbol=h.source_symbol, target_symbol=h.target_symbol,
                             direction=h.direction, reason=h.reason, source_url=h.source_url,
                             event_time=h.event_time, price_change_pct=h.price_change_pct)
        session.add(row)
        rows.append(row)
    if rows:
        await session.flush()
    return rows


# --- the run -------------------------------------------------------------------------

async def _default_news(session, symbols, *, cfg):
    from company_graph.news_events import refresh_news_events

    if not cfg.newsapi_key:
        raise RuntimeError("NEWSAPI_KEY is not set")
    return await refresh_news_events(session, symbols, cfg=cfg)


async def _default_social(session, symbols, *, cfg):
    from company_graph.social_events import refresh_social_events

    if not cfg.x_bearer_token:
        return []  # X is optional: without a key the step is simply left out
    return await refresh_social_events(session, symbols, cfg=cfg)


async def _default_market(session, symbols, *, cfg, directory=None):
    from company_graph.market_events import fetch_market_events

    return await fetch_market_events(symbols, session=session, cfg=cfg, directory=directory)


async def _step(name: str, session, coro_fn, result: HighlightRunResult) -> None:
    """Run one optional refresh step; commit on success, roll back and log on failure."""
    if coro_fn is None:
        return
    try:
        await coro_fn()
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - optional input; never fails the run
        logger.warning("highlights for %s: %s events skipped: %s", result.symbol, name, exc)
        result.steps_failed.append(name)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            logger.exception("rollback after the %s step failed", name)


async def load_events(session, symbols: Sequence[str], since: datetime) -> list[GraphEvent]:
    """graph_events for these companies since `since`, newest first. Read-only."""
    if not symbols:
        return []
    return list((await session.execute(
        select(GraphEvent).where(GraphEvent.entity_symbol.in_(list(symbols)), GraphEvent.occurred_at >= since)
        .order_by(GraphEvent.occurred_at.desc(), GraphEvent.id.desc())
    )).scalars().all())


async def build_highlights(
    symbol: str,
    *,
    session,
    cfg: config_mod.Config | None = None,
    directory=None,
    now: datetime | None = None,
    refresh_news: Callable | None = _default_news,
    refresh_market: Callable | None = _default_market,
    refresh_social: Callable | None = _default_social,
    complete: Callable | None = None,
    price_gateway: Any = "env",
    fetch_prices: Callable | None = None,
    store: EvalStore | None = None,
) -> HighlightRunResult:
    """Build and save the highlights for `symbol`'s graph. See the module docstring.

    Commits as it goes (give it its own session). Injectable for tests: `refresh_news(session,
    symbols, cfg=)`, `refresh_market(session, symbols, cfg=, directory=)` and
    `refresh_social(session, symbols, cfg=)` (None skips the step),
    `complete` (llm.complete's signature), `price_gateway` ("env" reads the Alpaca keys; None
    disables prices), `fetch_prices` (fetch_price_series' signature) and `store`.
    """
    cfg = cfg or config_mod.load()
    sym = symbol.strip().upper()
    result = HighlightRunResult(symbol=sym)
    if cfg.fake:
        return result
    now = _utc(now or datetime.now(timezone.utc))

    stored = await graph_links.read_links(session, sym, cfg.graph_max_linked)
    linked = list(dict.fromkeys(l.symbol for l in stored))
    result.linked = len(linked)
    if not linked:
        return result
    symbols = [sym, *linked]

    news_fn = (lambda: refresh_news(session, symbols, cfg=cfg)) if refresh_news else None
    market_fn = (lambda: refresh_market(session, symbols, cfg=cfg, directory=directory)) if refresh_market else None
    social_fn = (lambda: refresh_social(session, symbols, cfg=cfg)) if refresh_social else None
    await _step("news", session, news_fn, result)
    await _step("market", session, market_fn, result)
    await _step("social", session, social_fn, result)

    events = await load_events(session, symbols, now - timedelta(seconds=cfg.event_window_s))
    result.events = len(events)
    if not events:
        return result

    names = {l.symbol: l.name or l.symbol for l in stored}
    sym_name = sym
    if directory is not None:
        company = directory.get(sym)
        sym_name = company.name if company else sym
    names[sym] = sym_name

    store = store or EvalStore()
    async with _EVAL_LOCK:
        pending = []
        for ev in events:
            cands = candidates_for(ev.entity_symbol, sym, sym_name, stored)
            if not cands or not (ev.url and ev.url.strip()):
                continue
            if store.judged(sym, ev.entity_symbol, ev.url, [c.symbol for c in cands]):
                result.reused += 1
                continue
            pending.append((ev, cands))
    pending = pending[:MAX_EVENTS_PER_RUN]
    if not pending:
        return result

    sem = asyncio.Semaphore(MAX_PARALLEL_MODEL_CALLS)

    async def judge(ev, cands):
        async with sem:
            try:
                answer = await asyncio.to_thread(ask_model, ev, names.get(ev.entity_symbol, ev.entity_symbol),
                                                 cands, complete)
                return answer
            except LlmError as exc:
                logger.warning("highlights for %s: model failed on %s: %s", sym, ev.url, exc)
                return None

    answers = await asyncio.gather(*(judge(ev, c) for ev, c in pending))

    gateway = alpaca_gateway_from_env() if price_gateway == "env" else price_gateway
    price_cache: dict = {}
    new: list[NewHighlight] = []
    judged: list[tuple] = []
    for (ev, cands), answer in zip(pending, answers):
        result.evaluated += 1
        if answer is None:
            result.model_errors += 1
            continue
        judged.append((ev, cands))
        by_symbol = {c.symbol: c for c in cands}
        seen: set[str] = set()
        for inv in answer.involved:
            cand = by_symbol.get(inv.symbol.strip().upper()) or by_symbol.get(inv.symbol.strip())
            if cand is None or cand.symbol in seen:
                continue
            direction = direction_for(cand.role, inv.competitive_gain)
            reason = _clean_reason(inv.reason)
            if direction is None or not is_factual(reason):
                continue
            seen.add(cand.symbol)
            pct = await price_change_pct(cand.symbol, ev.occurred_at, now, gateway, fetch_prices, price_cache,
                                         directory)
            new.append(NewHighlight(event_id=ev.id, source_symbol=ev.entity_symbol, target_symbol=cand.symbol,
                                    direction=direction, reason=reason, source_url=ev.url,
                                    event_time=_utc(ev.occurred_at), price_change_pct=pct))

    rows = await save_highlights(session, new)
    await session.commit()
    result.saved = len(rows)

    async with _EVAL_LOCK:
        for ev, cands in judged:
            store.record(sym, ev.entity_symbol, ev.url, [c.symbol for c in cands], now)
        store.prune(now, cfg.event_window_s)
        try:
            store.save()
        except OSError as exc:
            logger.warning("could not save highlight evaluations: %s", exc)
    return result
