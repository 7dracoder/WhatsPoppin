"""Company directory and ticker resolver, built from SEC's company_tickers.json.

The file is downloaded once, cached on disk under .cache/company_graph/, and
held in memory. Lookups (resolve, search_companies) are pure in-memory work.

Only companies that are searched or linked get an `entities` row
(ensure_entity); the full SEC list is never bulk-loaded into that table.
"""

import asyncio
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.entities import EntityAlias
from backend.entities.loader import load_entities

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "company_graph"
CACHE_FILE = CACHE_DIR / "company_tickers.json"
CACHE_MAX_AGE_S = 7 * 24 * 3600  # SEC refreshes the list daily; a week-old copy is fine

# Trailing words dropped before comparing names ("Tesla, Inc." == "Tesla").
_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies",
    "ltd", "limited", "plc", "llc", "lp", "llp", "sa", "ag", "nv", "se", "spa",
    "holdings", "holding", "group", "the", "de", "new", "com",
}
_STATE_TAG = re.compile(r"\s*[/\\][a-z]{2,3}[/\\]?$", re.IGNORECASE)  # SEC titles like "FOO CORP /DE/", "BAR INC/MD/"


@dataclass(frozen=True)
class Company:
    symbol: str
    name: str
    cik: int


def normalize_name(name: str) -> str:
    """Lowercase, drop punctuation and trailing corporate suffixes."""
    s = name.lower().strip()
    s = _STATE_TAG.sub("", s).strip()
    s = s.replace("&", " and ")
    s = re.sub(r"\.com\b", "", s)  # "Amazon.com, Inc." -> "amazon"
    s = re.sub(r"[^a-z0-9 ]+", " ", s.replace(".", ""))
    words = s.split()
    while words and words[-1] in _SUFFIXES:
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def short_name(name: str) -> str:
    """Display name without trailing suffixes, keeping the original case: "Tesla, Inc." -> "Tesla"."""
    s = _STATE_TAG.sub("", name.strip()).strip()
    words = s.split()
    # Drop trailing suffixes, and connectors they leave behind: "JPMORGAN CHASE & CO" -> "JPMORGAN CHASE".
    while words and (re.sub(r"[^a-z0-9]", "", words[-1].lower()) in _SUFFIXES | {"", "and"}):
        words.pop()
    return " ".join(words).rstrip(",.&/- ")


class CompanyDirectory:
    def __init__(self, companies: Iterable[Company], aliases: Iterable[EntityAlias] = ()):
        """`aliases`: common names SEC's legal names don't contain ("Google" for Alphabet), from
        backend/entities/data/sp500_top50.json by default (see get_directory). They join
        resolve() and search(), so the ticker box takes the name people actually type.
        """
        self._by_ticker: dict[str, Company] = {}
        self._by_name: dict[str, list[Company]] = {}
        self._ordered: list[tuple[str, str, Company]] = []  # (ticker lower, normalized name, company)
        # SEC's company_tickers.json is ordered by company size (largest first), so a company's
        # position in it is a free size ranking: 0 = largest. Keyed by CIK (one company, many tickers).
        self._by_cik: dict[int, Company] = {}
        self._size_rank: dict[int, int] = {}
        for c in companies:
            if not c.symbol or c.symbol in self._by_ticker:
                continue
            self._by_ticker[c.symbol] = c
            if c.cik not in self._by_cik:
                self._by_cik[c.cik] = c
                self._size_rank[c.cik] = len(self._size_rank)
            norm = normalize_name(c.name)
            if norm:
                self._by_name.setdefault(norm, []).append(c)
            self._ordered.append((c.symbol.lower(), norm, c))
        # (normalized alias, company), only for aliases of listed companies.
        self._aliases: list[tuple[str, Company]] = []
        for entry in aliases:
            company = self._by_ticker.get(entry.symbol.upper())
            if company is None:
                continue
            for alias in entry.aliases:
                norm = normalize_name(alias)
                if norm and norm not in self._by_name:
                    self._aliases.append((norm, company))

    @classmethod
    def from_sec_json(cls, data: dict[str, Any], aliases: Iterable[EntityAlias] = ()) -> "CompanyDirectory":
        """Parse SEC's {"0": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."}, ...}."""
        rows = data.values() if isinstance(data, dict) else data
        return cls(
            (Company(symbol=str(r["ticker"]).upper().strip(), name=str(r["title"]).strip(), cik=int(r["cik_str"]))
             for r in rows),
            aliases,
        )

    def __len__(self) -> int:
        return len(self._by_ticker)

    def get(self, symbol: str) -> Company | None:
        return self._by_ticker.get(symbol.upper().strip())

    def by_cik(self, cik: int) -> Company | None:
        """The company's first (most traded) listing for this CIK."""
        return self._by_cik.get(int(cik))

    def size_rank(self, cik: int) -> int:
        """Position by company size in SEC's list, 0 = largest. Unlisted companies rank after all listed ones."""
        return self._size_rank.get(int(cik), len(self._size_rank))

    def resolve(self, text: str) -> Company | None:
        """Exact ticker first, then a normalized name match. None when not confident."""
        raw = (text or "").strip()
        if not raw:
            return None
        token = raw.lstrip("$")
        # A ticker written in capitals ("TSLA", "$TSLA", "BRK.B") is taken literally.
        if " " not in token and token == token.upper():
            hit = self._by_ticker.get(token.replace(".", "-")) or self._by_ticker.get(token)
            if hit:
                return hit
        norm = normalize_name(raw)
        matches = self._by_name.get(norm, []) if norm else []
        if len({c.cik for c in matches}) == 1:
            return matches[0]  # several share classes of one issuer: SEC lists the main one first
        if matches:
            return None  # different issuers with the same name: ambiguous
        alias_hits = {c.cik: c for a, c in self._aliases if a == norm}
        if len(alias_hits) == 1:
            return next(iter(alias_hits.values()))
        # Lower-case single word ("tsla") that is not a company name.
        if " " not in token:
            return self._by_ticker.get(token.upper().replace(".", "-"))
        return None

    def search(self, prefix: str, limit: int = 10) -> list[Company]:
        """Ticker-prefix matches first (shortest ticker first), then alias-prefix matches ("goo" ->
        Google -> GOOGL), then name-prefix matches in SEC order, then names containing the word.
        """
        q = (prefix or "").strip().lower().lstrip("$")
        if not q or limit <= 0:
            return []
        qn = normalize_name(q) or q
        ticker_hits: list[Company] = []
        name_hits: list[Company] = []
        word_hits: list[Company] = []
        alias_hits: list[Company] = []
        for norm, c in self._aliases:
            if norm.startswith(qn) and c not in alias_hits:
                alias_hits.append(c)
        for tick, norm, c in self._ordered:
            if tick.startswith(q):
                ticker_hits.append(c)
            elif norm.startswith(qn):
                name_hits.append(c)
            elif len(word_hits) < limit and f" {qn}" in f" {norm}":
                word_hits.append(c)
        ticker_hits.sort(key=lambda c: len(c.symbol))
        seen: set[str] = set()
        ranked = [c for c in ticker_hits + alias_hits + name_hits + word_hits
                  if not (c.symbol in seen or seen.add(c.symbol))]
        return ranked[:limit]

    def aliases_for(self, symbols: Iterable[str]) -> list[EntityAlias]:
        out: list[EntityAlias] = []
        seen: set[str] = set()
        for sym in symbols:
            if sym in seen:
                continue
            seen.add(sym)
            c = self.get(sym)
            if c is None:
                # Non-US related companies use their name as the symbol, which the matcher already searches.
                out.append(EntityAlias(symbol=sym, aliases=[]))
                continue
            aliases = [c.name]
            short = short_name(c.name)
            if short and short.lower() != c.name.lower():
                aliases.append(short)
            out.append(EntityAlias(symbol=c.symbol, aliases=aliases))
        return out


# --- loading -----------------------------------------------------------------

def _fetch_sec_json(client_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Download through SecClient so the request gets the SEC User-Agent, rate limit and retries.

    load_sec_json keeps its own week-long cache, so the client's cache is off. Callers are
    synchronous but may sit inside a running event loop (an API handler), so in that case the
    download runs on a worker thread with its own loop.
    """
    from company_graph.sec import SecClient

    factory = client_factory or (lambda: SecClient(cache_dir=None))

    async def fetch() -> dict[str, Any]:
        async with factory() as sec:
            return await sec.get_json(SEC_TICKERS_URL)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(fetch())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, fetch()).result()


def load_sec_json(
    cache_file: Path = CACHE_FILE,
    fetch: Callable[[], dict[str, Any]] = _fetch_sec_json,
    max_age_s: float = CACHE_MAX_AGE_S,
) -> dict[str, Any]:
    """Read the on-disk copy if fresh, otherwise download and cache it. A stale copy beats a failed download."""
    fresh = cache_file.exists() and time.time() - cache_file.stat().st_mtime < max_age_s
    if fresh:
        return json.loads(cache_file.read_text())
    try:
        data = fetch()
    except Exception:
        if cache_file.exists():
            return json.loads(cache_file.read_text())
        raise
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(cache_file)
    return data


@lru_cache(maxsize=1)
def get_directory() -> CompanyDirectory:
    """The process-wide directory, loaded once, with the seed aliases so names like "Google" resolve."""
    return CompanyDirectory.from_sec_json(load_sec_json(), aliases=load_entities())


def resolve(text: str) -> Company | None:
    return get_directory().resolve(text)


def search_companies(prefix: str, limit: int = 10) -> list[Company]:
    return get_directory().search(prefix, limit)


def aliases_for(symbols: Iterable[str]) -> list[EntityAlias]:
    return get_directory().aliases_for(symbols)


# --- entities table ----------------------------------------------------------

async def ensure_entity(session, company: Company, entity_model=None):
    """Insert or update one `entities` row (type "company") for this company. Does not commit.

    `session` is a SQLAlchemy AsyncSession. `entity_model` defaults to backend.models.entity.Entity
    (imported lazily so this module works before that model exists).
    """
    if entity_model is None:
        from backend.models.entity import Entity as entity_model  # noqa: N813

    row = await session.get(entity_model, company.symbol)
    if row is None:
        row = entity_model(symbol=company.symbol, name=company.name, type="company")
        session.add(row)
    elif row.name != company.name:
        row.name = company.name
    return row
