"""F7. Prediction market events: alerts from the alert detector, mapped to the companies they name.

Read-only on the other modules' tables: this module only SELECTs from `alerts` and `markets` and
never writes to them (nor to `market_prices`, `market_trades`, `market_hourly`,
`market_baselines`). It does not judge odds itself; an `alerts` row already means "a market
moved". It writes only this feature's tables: `entities` (via ensure_entity),
`market_entities` and `graph_events`.

Flow:
    read_recent_alerts(session, since)       -> AlertRow (alert joined with its markets row)
    alert_texts(row)                         -> market title, event title, outcome label
    find_companies(text, directory)          -> companies named in the text (pure, see below)
    match_alert(row, directory)              -> MarketEvent per named company (pure)
    save_market_events(session, events)      -> market_entities + graph_events rows
    fetch_market_events(symbols, session=..) -> all of the above for the last
                                                GRAPH_EVENT_WINDOW_DAYS

How company names are found (precision over recall; a missed market costs a highlight, a
false match puts a wrong highlight on the graph):
  * Text is split into word spans (up to 6 words) that never cross punctuation such as
    "?", ":", "," or parentheses. Longer spans are tried first, so "Apple Hospitality REIT"
    wins over "Apple".
  * A span must look like a proper noun: every word has a capital letter or a digit, except
    connectors ("and", "&", "of", "the", ...) inside the span. "apple pie" never matches.
  * Each span goes through companies.resolve, and the hit is kept only if the span's
    normalized form equals the company's normalized name. That throws away resolve's ticker
    paths, so "ON", "IT", "ALL", "KEY" or "Snow" can never match a company by its ticker.
    Names that two issuers share stay ambiguous (resolve returns None).
  * Tickers count only as cashtags ("$TSLA").
  * Single words need at least 3 characters (or a digit, as in "3M") and must not be in
    COMMON_WORDS: SEC names that are also everyday words in market titles ("Target", "Gold",
    "Visa", "Progressive", "Dow", "Strategy", ...). COMMON_PHRASES does the same for a few
    multi-word names ("Public Policy", "US Gold", "Five Below").
  * the built-in seed aliases (backend/entities/data, e.g. "Google" -> GOOGL, "IBM", "AMD") are
    also tried, with the same rules; all-capital aliases must appear in capitals.
    EntityMatcher itself is not used: it also matches bare symbols case-insensitively, so
    "V" or "MA" would match almost any title.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import and_, select

from backend.models.alert import Alert
from backend.models.graph_event import GraphEvent
from backend.models.market import Market
from backend.models.market_entity import MarketEntity
from company_graph import companies
from company_graph.companies import Company, CompanyDirectory, normalize_name

__all__ = [
    "AlertRow", "MarketEvent", "CompanyMatch", "find_companies", "alert_texts", "match_alert",
    "read_recent_alerts", "save_market_events", "fetch_market_events",
    "COMMON_WORDS", "COMMON_PHRASES", "EVENT_SOURCE", "EVENT_TYPE",
]

EVENT_SOURCE = "market"
EVENT_TYPE = "odds_move"
MAX_SPAN_WORDS = 6

# Single-word SEC company names (after normalize_name) that are ordinary words in market
# titles. Built from the SEC list's one-word names that are dictionary words, minus brands
# whose word sense rarely shows up in a market title (apple, oracle, chevron, ...).
COMMON_WORDS = frozenset("""
acuity aes affirm agora alarm allot amaze amplitude angle apa arm authentic aware awareness ball
banner bark beeline bill block bonk booking brunswick buckle bullish cadre calumet canon capstone
carnival cheer city click cluster coffee coherent commerce compass conduit cooper copa cosmos
crane crown decent deluxe diploma dixie dover dow dreamland eastern elastic enact enhanced ensign
eon equitable escalade eve exponent farmhouse fathom flex fluent fold forge fortis fossil founder
fox freedom frequency frontier fuse gap gauzy gee genius geo glow gogo gold grab graham grail
grandstand gravity happen harmonic harrow hayward helio hello here highway hilltop hippo honest
hub huntsman ibex icon immersion innovate integer intelligent interface intrusion joint kestrel
largo latch lear leet lemonade light lineage lion lionheart lithium lucent maiden match maximus
meridian merlin mint morgan mosaic navigator neutron newmarket news nice nip noble nova oculus on
opera orange ouster outdoor owlet pattern pennant penumbra people perfect plexus pony pool popular
porch positron post progressive quantum radian rectitude reformation reliability reliance repay
revolve rocket root rum sap scholastic screen sea seaboard seek seer senior sentinel shell snail
snap sound southern southland spar specificity spire star stem strategy stride strive sun
tapestry target team ten tenable tidewater tiny toast toro torrid track trip tron universal
upstart vale vat vertex viking visa visionary weed weir wise woodward workday
abbott berkshire chase coke lilly
bitcoin ethereum solana election president senate house rain snow
""".split())

# Multi-word SEC names (normalized) that read as ordinary phrases in market titles.
COMMON_PHRASES = frozenset({
    "life time", "start today", "open house", "public policy", "fast track", "high tide",
    "pop culture", "hold me", "my size", "our bond", "first national", "national bank",
    "independent bank", "happy city", "sow good", "five below", "good gaming", "top financial",
    "world road", "us gold", "gold reserve", "white gold", "blue gold", "star gold", "sky gold",
    "gold rock", "nation gold", "rise gold", "net power", "one gas", "data storage",
    "natural resource", "energy recovery", "waste energy", "quantum x", "core ai", "global ai",
    "light ai", "clean vision", "red cat", "real messenger",
})

# Phrases that contain a company name but mean something else; their words are skipped.
NOT_COMPANIES = frozenset({"big apple", "amazon rainforest", "amazon river", "amazon basin"})

_CONNECTORS = {"and", "&", "of", "the", "de", "for", "la", "du", "von", "van"}
_SEGMENT_SPLIT = re.compile(r"[^\w&.'\-$ ]+")  # "?", ":", ",", "(", ")", "/", quotes, ...
_CASHTAG = re.compile(r"\$([A-Z]{1,5}(?:[.-][A-Z]{1,2})?)\b")


@dataclass(frozen=True)
class CompanyMatch:
    company: Company
    text: str  # the words in the title that named it


@dataclass(frozen=True)
class AlertRow:
    """One `alerts` row with the fields this feature needs, plus its `markets` row (if any)."""
    alert_id: int
    created_at: datetime
    source: str
    market_id: str
    summary: str
    context: dict
    market_title: str | None = None
    market_event_title: str | None = None
    market_outcome_label: str | None = None
    market_url: str | None = None


@dataclass(frozen=True)
class MarketEvent:
    """What becomes one `graph_events` row (and one `market_entities` row)."""
    entity_symbol: str
    company_name: str
    matched_text: str
    title: str          # the alert's summary
    url: str | None     # the market's page; None means no graph_events row can be saved
    occurred_at: datetime  # the alert's created_at, in UTC
    alert_id: int
    source: str         # market platform, e.g. "kalshi" (graph_events.source is always "market")
    market_id: str


# --- finding company names in text (pure) --------------------------------------------

def _word_ok(word: str) -> bool:
    return any(ch.isupper() or ch.isdigit() for ch in word)


def _variants(span: str) -> list[str]:
    """The span, with a possessive dropped ("Apple's"), and with apostrophes removed ("McDonald's")."""
    out = [span]
    if span.endswith("'s"):
        out.append(span[:-2])
    if "'" in span:
        out.append(span.replace("'", ""))
    return out


def _allowed(norm: str) -> bool:
    if not norm or norm in COMMON_PHRASES:
        return False
    if " " not in norm:
        return norm not in COMMON_WORDS and (len(norm) >= 3 or any(c.isdigit() for c in norm))
    return True


def _alias_index(directory: CompanyDirectory, aliases: Iterable | None) -> dict[str, list[tuple[str, Company]]]:
    """normalized alias -> [(alias as written, company)], for aliases whose symbol is in the directory."""
    index: dict[str, list[tuple[str, Company]]] = {}
    for entry in aliases or ():
        company = directory.get(entry.symbol) or directory.get(entry.symbol.replace(".", "-"))
        if company is None:
            continue
        for alias in entry.aliases:
            norm = normalize_name(alias)
            if _allowed(norm):
                index.setdefault(norm, []).append((alias, company))
    return index


def _lookup(span: str, directory: CompanyDirectory, alias_index: dict) -> Company | None:
    for cand in _variants(span):
        norm = normalize_name(cand)
        if not _allowed(norm):
            continue
        hit = directory.resolve(cand)
        # Keep only name matches: resolve also tries the span as a ticker.
        if hit is not None and normalize_name(hit.name) == norm:
            return hit
        for written, company in alias_index.get(norm, ()):
            if written.isupper() and cand != written:
                continue  # "IBM" must be written "IBM"
            return company
    return None


def find_companies(text: str | None, directory: CompanyDirectory,
                   aliases: Iterable | None = None) -> list[CompanyMatch]:
    """Companies named in a market or event title, in order of first mention, one per symbol.

    `aliases` is an optional list of backend.entities.EntityAlias (extra names per symbol).
    """
    if not text:
        return []
    text = text.replace("’", "'")
    alias_index = _alias_index(directory, aliases) if aliases else {}
    found: dict[str, CompanyMatch] = {}

    for tag in _CASHTAG.findall(text):
        hit = directory.resolve("$" + tag)
        if hit is not None and hit.symbol in (tag, tag.replace(".", "-")):
            found.setdefault(hit.symbol, CompanyMatch(hit, "$" + tag))

    for segment in _SEGMENT_SPLIT.split(text):
        words = [w.strip(".-'") for w in segment.split()]
        words = [w for w in words if w and not w.startswith("$")]
        i = 0
        while i < len(words):
            matched = 0
            for n in range(min(MAX_SPAN_WORDS, len(words) - i), 0, -1):
                span_words = words[i:i + n]
                if normalize_name(" ".join(span_words)) in NOT_COMPANIES:
                    matched = n  # "Big Apple" is New York, not Apple
                    break
                if span_words[0].lower() in _CONNECTORS or span_words[-1].lower() in _CONNECTORS:
                    continue
                if not all(_word_ok(w) or w.lower() in _CONNECTORS for w in span_words):
                    continue
                company = _lookup(" ".join(span_words), directory, alias_index)
                if company is not None:
                    found.setdefault(company.symbol, CompanyMatch(company, " ".join(span_words)))
                    matched = n
                    break
            i += matched or 1
    return list(found.values())


# --- alerts -> events (pure) ---------------------------------------------------------

def _as_dict(context: Any) -> dict:
    if isinstance(context, dict):
        return context
    if isinstance(context, str):
        try:
            value = json.loads(context)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def alert_texts(row: AlertRow) -> list[str]:
    """Market title, event title and outcome label, from alerts.context and the markets row.

    The outcome label is included because multi-outcome events often name the company only
    there ("Which company will be worth the most?" / outcome "Nvidia").
    """
    market = _as_dict(row.context).get("market") or {}
    texts = [market.get("title"), market.get("event_title"), market.get("outcome_label"),
             row.market_title, row.market_event_title, row.market_outcome_label]
    out: list[str] = []
    for t in texts:
        if isinstance(t, str) and t.strip() and t not in out:
            out.append(t)
    return out


def alert_url(row: AlertRow) -> str | None:
    """The market's page: the markets row first, then the copy in alerts.context."""
    url = row.market_url or (_as_dict(row.context).get("market") or {}).get("url")
    return url if isinstance(url, str) and url.strip() else None


def match_alert(row: AlertRow, directory: CompanyDirectory,
                aliases: Iterable | None = None) -> list[MarketEvent]:
    """One MarketEvent per listed company named in the alert's market or event title."""
    found: dict[str, CompanyMatch] = {}
    for text in alert_texts(row):
        for m in find_companies(text, directory, aliases):
            found.setdefault(m.company.symbol, m)
    url = alert_url(row)
    return [
        MarketEvent(entity_symbol=m.company.symbol, company_name=m.company.name, matched_text=m.text,
                    title=row.summary, url=url, occurred_at=_utc(row.created_at), alert_id=row.alert_id,
                    source=row.source, market_id=row.market_id)
        for m in found.values()
    ]


# --- database ------------------------------------------------------------------------

def recent_alerts_query(since: datetime):
    """SELECT only: alerts created since `since`, each with its markets row (outer join), oldest first."""
    return (
        select(Alert.id, Alert.created_at, Alert.source, Alert.market_id, Alert.summary, Alert.context,
               Market.title, Market.event_title, Market.outcome_label, Market.url)
        .select_from(Alert)
        .outerjoin(Market, and_(Market.source == Alert.source, Market.market_id == Alert.market_id))
        .where(Alert.created_at >= since)
        .order_by(Alert.created_at, Alert.id)
    )


async def read_recent_alerts(session, since: datetime) -> list[AlertRow]:
    """Read-only. `session` is a SQLAlchemy AsyncSession (or anything with an async execute)."""
    result = await session.execute(recent_alerts_query(since))
    return [
        AlertRow(alert_id=r[0], created_at=r[1], source=r[2], market_id=r[3], summary=r[4] or "",
                 context=_as_dict(r[5]), market_title=r[6], market_event_title=r[7],
                 market_outcome_label=r[8], market_url=r[9])
        for r in result.all()
    ]


async def save_market_events(session, events: Iterable[MarketEvent], directory: CompanyDirectory) -> int:
    """Upsert `entities`, `market_entities` and `graph_events` rows. Flushes, does not commit.

    One graph_events row per (company, market URL), the table's unique key: a later alert on
    the same market replaces the title, time and alert_id of the earlier one. Events without
    a market URL get a market_entities row only. Returns the number of graph_events rows
    inserted or updated.
    """
    saved = 0
    for ev in events:
        company = directory.get(ev.entity_symbol) or Company(ev.entity_symbol, ev.company_name, 0)
        await companies.ensure_entity(session, company)
        await session.flush()  # entities row before the market_entities foreign key
        key = (ev.source, ev.market_id, ev.entity_symbol)
        if await session.get(MarketEntity, key) is None:
            session.add(MarketEntity(source=ev.source, market_id=ev.market_id, entity_symbol=ev.entity_symbol))
        if not ev.url:
            continue
        existing = (await session.execute(
            select(GraphEvent).where(GraphEvent.entity_symbol == ev.entity_symbol, GraphEvent.url == ev.url)
        )).scalars().first()
        if existing is None:
            session.add(GraphEvent(entity_symbol=ev.entity_symbol, source=EVENT_SOURCE, event_type=EVENT_TYPE,
                                   title=ev.title, url=ev.url, occurred_at=ev.occurred_at, alert_id=ev.alert_id))
            saved += 1
        elif existing.source == EVENT_SOURCE and _utc(existing.occurred_at) <= ev.occurred_at:
            existing.event_type = EVENT_TYPE
            existing.title = ev.title
            existing.occurred_at = ev.occurred_at
            existing.alert_id = ev.alert_id
            saved += 1
        await session.flush()
    return saved


@asynccontextmanager
async def _open_session(database_url: str):
    """A session on the shared database, with this feature's tables created. Commits on success."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from company_graph.db import create_tables, make_engine

    if not database_url:
        raise RuntimeError("DATABASE_URL is not set; market events read the shared database")
    engine = make_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(create_tables)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
            await session.commit()
    finally:
        await engine.dispose()


async def fetch_market_events(symbols: Iterable[str] | None = None, *, session=None, now: datetime | None = None,
                              cfg=None, directory: CompanyDirectory | None = None,
                              aliases: Iterable | None = None) -> list[MarketEvent]:
    """Map the last GRAPH_EVENT_WINDOW_DAYS of alerts to companies and store them.

    `symbols`: only these companies are matched, stored and returned (None = every listed
    company found). With `session` (an AsyncSession) the caller owns the transaction: rows
    are flushed, not committed. Without one, a session on DATABASE_URL is opened and
    committed. In fake mode without a session, nothing is read and [] is returned.
    """
    if cfg is None:
        from company_graph.config import load
        cfg = load()
    if session is None and cfg.fake:
        return []
    if directory is None:
        directory = companies.get_directory()
    if aliases is None:
        from backend.entities import load_entities
        aliases = load_entities()
    wanted = None
    if symbols is not None:
        wanted = set()
        for s in symbols:
            c = directory.get(s) or directory.get(s.replace(".", "-"))
            wanted.add(c.symbol if c else s.upper().strip())

    now = _utc(now or datetime.now(timezone.utc))
    since = now - timedelta(days=cfg.graph_event_window_days)

    async def run(s) -> list[MarketEvent]:
        events: list[MarketEvent] = []
        for row in await read_recent_alerts(s, since):
            events.extend(e for e in match_alert(row, directory, aliases)
                          if wanted is None or e.entity_symbol in wanted)
        await save_market_events(s, events, directory)
        return events

    if session is not None:
        return await run(session)
    async with _open_session(cfg.database_url) as own:
        return await run(own)
