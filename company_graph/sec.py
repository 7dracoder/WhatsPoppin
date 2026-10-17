"""Async SEC EDGAR client: company submissions, filing text and full-text search.

Every request carries a User-Agent with SEC_CONTACT_EMAIL (SEC rejects anonymous
traffic) and passes through one shared limiter of 5 requests per second, under
SEC's published cap of 10. 429 and 503 responses are retried with backoff.
Responses are cached on disk under .cache/company_graph/, keyed by URL.

    async with SecClient() as sec:
        result = await sec.list_filings(1318605, forms=["10-K"])
        text = await sec.fetch_text(result.filings[0].url)
        hits = await sec.full_text_search('"sole supplier"', forms=["10-K"], since=date(2025, 1, 1))

The full-text search endpoint (efts.sec.gov/LATEST/search-index) is undocumented.
Shape confirmed on 2026-10-10:
    params: q (phrase in double quotes), forms (comma separated), dateRange=custom,
            startdt / enddt (YYYY-MM-DD), from (offset). Page size is fixed at 100.
    response: Elasticsearch-style {"hits": {"total": {"value": n}, "hits": [
        {"_id": "<adsh>:<file name>", "_source": {"adsh", "ciks", "display_names",
         "form", "root_forms", "file_type", "file_date", "period_ending", "sics", ...}}]}}
Parsing is defensive: a malformed hit is skipped, not raised.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import weakref
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import urlencode

import httpx

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{document}"
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
EFTS_PAGE_SIZE = 100  # fixed by the endpoint; "from" pages through results
BROWSE_URL = "https://www.sec.gov/cgi-bin/browse-edgar"  # company lists by industry (SIC) code
BROWSE_PAGE_SIZE = 100

DEFAULT_CACHE_DIR = Path(".cache/company_graph")
DEFAULT_CACHE_TTL_S = 24 * 3600  # submissions and search results change; archived filings never do
RETRY_STATUSES = (429, 500, 502, 503, 504)  # rate limits and SEC's passing server errors


class SecConfigError(RuntimeError):
    """SEC_CONTACT_EMAIL is missing, so no request may be sent."""


class SecRequestError(RuntimeError):
    """SEC kept answering 429/503 after every retry."""


@dataclass(frozen=True)
class Filing:
    accession_number: str
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str
    url: str


@dataclass(frozen=True)
class FilingList:
    """list_filings result. sic_code, sic_description and listing_venue feed graph_company_profiles."""

    cik: int
    name: str
    sic_code: str | None
    sic_description: str | None
    tickers: list[str]
    listing_venue: str | None
    filings: list[Filing] = field(default_factory=list)  # newest first


@dataclass(frozen=True)
class SearchHit:
    accession_number: str
    cik: int
    company_name: str
    tickers: list[str]
    form: str
    filing_date: date | None
    document: str
    url: str
    sic_code: str | None


class RateLimiter:
    """At most `rate` acquisitions in any `period`-second window, shared by every request."""

    def __init__(
        self,
        rate: int = 5,
        period: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.rate = rate
        self.period = period
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._stamps and self._stamps[0] <= now - self.period:
                    self._stamps.popleft()
                if len(self._stamps) < self.rate:
                    self._stamps.append(now)
                    return
                await self._sleep(self._stamps[0] + self.period - now)


# One limiter per event loop, shared by every SecClient that isn't handed its own. A link run, a
# board run and the hop expansion's extra runs each open their own client; with a limiter apiece
# they would add up past SEC's limit (10 requests a second, per IP) even though each one obeyed it.
_SHARED_LIMITERS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, RateLimiter]" = weakref.WeakKeyDictionary()


def shared_limiter() -> RateLimiter:
    """The running event loop's limiter (a lock belongs to one loop, so it can't be a plain global)."""
    loop = asyncio.get_running_loop()
    limiter = _SHARED_LIMITERS.get(loop)
    if limiter is None:
        limiter = _SHARED_LIMITERS[loop] = RateLimiter()
    return limiter


class SecClient:
    def __init__(
        self,
        contact_email: str | None = None,
        *,
        cache_dir: Path | str | None = DEFAULT_CACHE_DIR,
        cache_ttl_s: float = DEFAULT_CACHE_TTL_S,
        limiter: RateLimiter | None = None,
        max_retries: int = 4,
        backoff_s: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout_s: float = 30.0,
    ):
        email = (contact_email if contact_email is not None else os.environ.get("SEC_CONTACT_EMAIL", "")).strip()
        if not email:
            raise SecConfigError(
                "SEC_CONTACT_EMAIL is not set. SEC requires a contact email in the User-Agent; "
                "add SEC_CONTACT_EMAIL=you@example.com to .env."
            )
        self.user_agent = f"WhatsPoppin company-graph {email}"
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.cache_ttl_s = cache_ttl_s
        # An injected sleep (tests) gets a limiter that uses it; otherwise the process-wide one,
        # looked up per request because the event loop isn't known yet.
        self.limiter = limiter or (RateLimiter(sleep=sleep) if sleep is not asyncio.sleep else None)
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self) -> SecClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- public API ----

    async def list_filings(
        self, cik: int | str, forms: Iterable[str] | None = None, since: date | None = None
    ) -> FilingList:
        """Recent filings for one company, newest first, filtered by form and filing date.

        Reads only the "recent" block of the submissions file (the last 1000 filings or one
        year, whichever is more), which always covers the latest 10-K and 10-Qs.
        """
        cik_int = int(cik)
        data = json.loads(await self._get(SUBMISSIONS_URL.format(cik=cik_int)))
        wanted = {f.upper() for f in forms} if forms else None
        recent = (data.get("filings") or {}).get("recent") or {}
        columns = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
        rows = zip(*(recent.get(c) or [] for c in columns))

        filings = []
        for accession, form, filed, reported, document in rows:
            filed_date = _parse_date(filed)
            if filed_date is None or (wanted and form.upper() not in wanted):
                continue
            if since and filed_date < since:
                continue
            filings.append(
                Filing(
                    accession_number=accession,
                    form=form,
                    filing_date=filed_date,
                    report_date=_parse_date(reported),
                    primary_document=document,
                    url=filing_url(cik_int, accession, document) if document else "",
                )
            )
        filings.sort(key=lambda f: f.filing_date, reverse=True)

        exchanges = [e for e in data.get("exchanges") or [] if e]
        return FilingList(
            cik=cik_int,
            name=data.get("name") or "",
            sic_code=str(data["sic"]) if data.get("sic") else None,
            sic_description=data.get("sicDescription") or None,
            tickers=[t for t in data.get("tickers") or [] if t],
            listing_venue=exchanges[0] if exchanges else None,
            filings=filings,
        )

    async def ciks_by_sic(self, sic_code: str, max_pages: int = 5) -> tuple[str, list[int]]:
        """CIKs SEC files under one industry (SIC) code, from EDGAR's company browse feed.

        Returns (url of the first page, CIKs in SEC's order). The feed garbles company names, so
        callers map CIKs to companies themselves. Confirmed live 2026-10-10: Atom XML with one
        <cik> per <company-info>, 100 per page, paged with `start`.
        """
        base = {"action": "getcompany", "SIC": str(sic_code), "owner": "include",
                "count": str(BROWSE_PAGE_SIZE), "output": "atom"}
        first_url = f"{BROWSE_URL}?{urlencode(sorted(base.items()))}"
        ciks: list[int] = []
        for page in range(max_pages):
            params = dict(base, **({"start": str(page * BROWSE_PAGE_SIZE)} if page else {}))
            found = [int(c) for c in _CIK_TAG.findall((await self._get(BROWSE_URL, params)).decode("latin-1"))]
            ciks.extend(c for c in found if c not in ciks)
            if len(found) < BROWSE_PAGE_SIZE:
                break
        return first_url, ciks

    async def get_json(self, url: str) -> Any:
        """Any SEC JSON file (e.g. company_tickers.json), with the same User-Agent, limiter, retries and cache."""
        return json.loads(await self._get(url))

    async def fetch_raw(self, url: str) -> str:
        """Download a document as it is, markup included (e.g. the XML of a Form 3 or 4)."""
        raw = await self._get(url)
        return raw.decode("utf-8", errors="replace")

    async def fetch_text(self, url: str) -> str:
        """Download a filing document and return its visible text."""
        raw = await self._get(url)
        return html_to_text(raw.decode("utf-8", errors="replace"))

    async def full_text_search(
        self,
        query: str,
        forms: Iterable[str] | None = None,
        since: date | None = None,
        limit: int = 100,
    ) -> list[SearchHit]:
        """Documents matching `query` (wrap phrases in double quotes), most relevant first."""
        base = {"q": query}
        if forms:
            base["forms"] = ",".join(forms)
        if since:
            base.update(dateRange="custom", startdt=since.isoformat(), enddt=date.today().isoformat())

        hits: list[SearchHit] = []
        offset = 0
        while len(hits) < limit:
            params = dict(base, **({"from": str(offset)} if offset else {}))
            data = json.loads(await self._get(EFTS_URL, params))
            page = ((data.get("hits") or {}).get("hits")) or []
            for raw in page:
                hit = _parse_search_hit(raw)
                if hit:
                    hits.append(hit)
            total = _total(data)
            offset += EFTS_PAGE_SIZE
            if len(page) < EFTS_PAGE_SIZE or (total is not None and offset >= total):
                break
        return hits[:limit]

    # ---- transport ----

    async def _get(self, url: str, params: dict[str, str] | None = None) -> bytes:
        full_url = f"{url}?{urlencode(sorted(params.items()))}" if params else url
        cached = self._cache_read(full_url)
        if cached is not None:
            return cached

        for attempt in range(self.max_retries + 1):
            await (self.limiter or shared_limiter()).acquire()
            resp = await self._http.get(full_url)
            if resp.status_code in RETRY_STATUSES:
                if attempt == self.max_retries:
                    break
                await self._sleep(_retry_delay(resp, self.backoff_s * 2**attempt))
                continue
            resp.raise_for_status()
            self._cache_write(full_url, resp.content)
            return resp.content
        raise SecRequestError(f"SEC returned {resp.status_code} for {full_url} after {self.max_retries} retries")

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / hashlib.sha256(url.encode()).hexdigest()

    def _cache_read(self, url: str) -> bytes | None:
        path = self._cache_path(url)
        if path is None or not path.exists():
            return None
        immutable = "/Archives/edgar/data/" in url
        if not immutable and time.time() - path.stat().st_mtime > self.cache_ttl_s:
            return None
        return path.read_bytes()

    def _cache_write(self, url: str, body: bytes) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)


# ---- pure helpers ----


def filing_url(cik: int | str, accession_number: str, document: str) -> str:
    return ARCHIVES_URL.format(
        cik=int(cik), accession_nodash=accession_number.replace("-", ""), document=document
    )


_SKIP_TAGS = {"script", "style", "head", "title", "ix:header"}
_BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "ul", "ol", "hr", "pre", "blockquote",
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Visible text of an HTML (or inline XBRL) document, one line per block, whitespace collapsed."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


_DISPLAY_NAME = re.compile(r"^(?P<name>.*?)\s*(?:\((?P<tickers>[^()]*)\))?\s*\(CIK (?P<cik>\d+)\)\s*$")


_CIK_TAG = re.compile(r"<cik>\s*(\d+)\s*</cik>")


def _parse_search_hit(raw: dict) -> SearchHit | None:
    try:
        src = raw.get("_source") or {}
        doc_id = raw.get("_id") or ""
        accession, _, document = doc_id.partition(":")
        accession = src.get("adsh") or accession
        ciks = src.get("ciks") or []
        if not accession or not document or not ciks:
            return None
        cik = int(ciks[0])
        display = (src.get("display_names") or [""])[0]
        m = _DISPLAY_NAME.match(display)
        name = (m.group("name") if m else display).strip()
        tickers = [t.strip() for t in (m.group("tickers") or "").split(",") if t.strip()] if m else []
        sics = src.get("sics") or []
        return SearchHit(
            accession_number=accession,
            cik=cik,
            company_name=name,
            tickers=tickers,
            form=src.get("form") or src.get("file_type") or "",
            filing_date=_parse_date(src.get("file_date")),
            document=document,
            url=filing_url(cik, accession, document),
            sic_code=str(sics[0]) if sics else None,
        )
    except (AttributeError, TypeError, ValueError):
        return None


def _total(data: dict) -> int | None:
    total = (data.get("hits") or {}).get("total")
    if isinstance(total, dict):
        total = total.get("value")
    return total if isinstance(total, int) else None


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _retry_delay(resp: httpx.Response, fallback: float) -> float:
    try:
        return max(0.0, float(resp.headers.get("Retry-After", "")))
    except ValueError:
        return fallback
