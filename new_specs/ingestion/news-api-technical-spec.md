
# Technical Spec: `ingestion/news_api`

**Status:** Draft
**Parent spec:** [news-aggregator.md](./news-aggregator.md) — read that first for the
*why* behind these choices (provider pick, which filters are in/out of scope,
the `q`-string collapsing, the article-ID gotcha). This doc is the *how*:
concrete files, function signatures, and error handling for someone to
implement directly.

**Scope:** the single-poll business logic — filters in, normalized
`ContentItem`s out. No scheduler, no cross-poll de-dup, no content-quality
filtering (see parent spec's Open Questions for why those are excluded).

---

## File layout

```
ingestion/news_api/
  __init__.py
  models.py          # data models + exceptions
  query_builder.py    # NewsQueryFilters -> newsapi-python call kwargs
  client.py           # NewsApiGateway: wraps NewsApiClient
  normalize.py        # raw article dict -> ContentItem
  entity_match.py      # substring/alias matcher
  service.py           # orchestrates the above into one poll_news() call
tests/ingestion/news_api/
  test_query_builder.py
  test_normalize.py
  test_entity_match.py
  test_client.py        # mocks NewsApiClient, no real HTTP calls
  test_service.py
  fixtures/
    sample_article.json       # one raw NewsAPI article, for normalize/client tests
    sample_everything_response.json  # a full /v2/everything response body
```

`service.py` is the only module other parts of the codebase should import
from — everything else is an implementation detail reachable through it.

---

## `models.py`

Uses **Pydantic**, not plain dataclasses — reasoning: this module sits at an
external boundary (caller-supplied filters, API-supplied JSON), and Pydantic
gives free validation (reject `page_size=500` or 25 source IDs at
construction time, not three calls deep inside `query_builder`) instead of
hand-written `if` checks.

```python
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, field_validator


class SortBy(str, Enum):
    RELEVANCY = "relevancy"
    POPULARITY = "popularity"
    PUBLISHED_AT = "publishedAt"


class SearchField(str, Enum):
    TITLE = "title"
    DESCRIPTION = "description"
    CONTENT = "content"


class NewsQueryFilters(BaseModel):
    source_ids: list[str] | None = None
    keyword_query: str | None = None
    exact_phrases: list[str] | None = None
    boolean_terms: str | None = None
    search_in: list[SearchField] | None = None
    domains: list[str] | None = None
    exclude_domains: list[str] | None = None
    language: str = "en"
    from_time: datetime | None = None
    to_time: datetime | None = None
    sort_by: SortBy = SortBy.PUBLISHED_AT
    page_size: int = Field(default=100, ge=1, le=100)
    page: int = Field(default=1, ge=1)

    @field_validator("source_ids")
    @classmethod
    def _max_20_sources(cls, v):
        if v and len(v) > 20:
            raise ValueError("NewsAPI allows at most 20 source IDs per call")
        return v

    @field_validator("keyword_query", "exact_phrases", "boolean_terms")
    @classmethod
    def _at_least_one_query_term(cls, v, info):
        # Cross-field: enforced in query_builder.build_query_string instead —
        # Pydantic field_validators can't see sibling fields here without a
        # model_validator. Documented, not implemented as a field validator.
        return v


class ContentItem(BaseModel):
    source: str = "news"
    id: str                      # article url, see parent spec "Article IDs"
    author: str | None           # NewsAPI source.name (outlet), not a byline
    title: str
    text: str | None             # NewsAPI "content", truncated ~200 chars on free tier
    entities: list[str] = []
    url: str
    published_at: datetime


class NewsApiError(Exception):
    """Raised for any non-ok NewsAPI response. `retryable` is a signal for
    whatever scheduler calls this layer later — not acted on here."""
    def __init__(self, code: str, message: str, retryable: bool):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"[{code}] {message}")
```

**Cross-field validation note:** "at least one of `keyword_query` /
`exact_phrases` / `boolean_terms` must be set" is a cross-field rule
(Pydantic needs a `model_validator`, not a `field_validator`, to see
sibling fields). Decision: enforce it in
`query_builder.build_query_string` instead, where the error message can
say exactly *which* combination is empty — not here.

---

## `query_builder.py`

```python
def build_query_string(filters: NewsQueryFilters) -> str:
    """Combine keyword_query / exact_phrases / boolean_terms into the single
    `q` string /v2/everything expects. Raises ValueError if all three are
    empty — /everything requires a non-empty q when no sources/domains are
    set, and an empty query is almost always a caller bug, not valid input.
    """

def build_request_kwargs(filters: NewsQueryFilters) -> dict:
    """Map a NewsQueryFilters to the exact kwargs newsapi-python's
    NewsApiClient.get_everything() expects: q, sources, domains,
    exclude_domains, language, from_param, to, sort_by, page_size, page.
    Omits keys whose value is None rather than passing None through —
    the client library treats an explicit None differently from an
    omitted kwarg for some params.
    """
```

- `source_ids` → joined into `sources="id1,id2,..."` (comma-separated, per
  NewsAPI's format).
- `domains` / `exclude_domains` → same comma-join.
- `from_time` / `to_time` → passed through as `datetime` objects;
  `newsapi-python` serializes those to the right ISO format itself (see
  parent spec's client-library research) — no manual `.isoformat()` needed.
- `search_in` → joined into `searchIn="title,description"` if set.

---

## `client.py`

```python
class NewsApiGateway:
    def __init__(self, api_key: str, http_client: NewsApiClient | None = None):
        """http_client is injectable for tests (pass a mock/fake); defaults
        to a real NewsApiClient(api_key=api_key)."""

    def fetch_raw(self, filters: NewsQueryFilters) -> list[dict]:
        """Build kwargs via query_builder, call get_everything, return the
        raw `articles` list from the response body. Raises NewsApiError on
        any non-"ok" status — never returns a partial/error response to
        the caller silently."""

    def _translate_exception(self, exc: NewsAPIException) -> NewsApiError:
        """Map newsapi-python's NewsAPIException (exc.get('code')) to our
        NewsApiError, setting `retryable` per the table below."""
```

**Error code → `retryable` mapping** (per [NewsAPI&#39;s documented error
codes](https://newsapi.org/docs/errors)):

| NewsAPI`code`                                                                  | Meaning                | `retryable`                                                   |
| -------------------------------------------------------------------------------- | ---------------------- | --------------------------------------------------------------- |
| `rateLimited`                                                                  | Over the request quota | `True` (after backoff)                                        |
| `sourcesTooMany`                                                               | >20 source IDs         | `False` (caller bug — also caught earlier by Pydantic)       |
| `parametersMissing` / `parameterInvalid`                                     | Bad request shape      | `False`                                                       |
| `apiKeyMissing` / `apiKeyInvalid` / `apiKeyExhausted` / `apiKeyDisabled` | Auth/quota problem     | `False`                                                       |
| anything else / unknown                                                          | Unmapped               | `False` (fail closed — don't retry what we don't understand) |

---

## `normalize.py`

```python
def normalize_article(raw: dict) -> ContentItem:
    """Map one raw NewsAPI article dict to ContentItem.
    id/url    <- raw["url"]            (see parent spec: no native article ID)
    author    <- raw["source"]["name"]  (outlet, not a byline — raw["author"]
                                         is often null or an unreliable byline
                                         string, so source name is the
                                         trustworthy field)
    title     <- raw["title"]
    text      <- raw["content"]         (may be None; truncated on free tier)
    published_at <- raw["publishedAt"]  (ISO 8601 -> datetime)
    entities  <- [] (filled in by service.py after entity_match runs,
                      not here — normalize stays a pure 1:1 field mapping)
    Raises ValueError if raw["url"] or raw["title"] is missing/empty —
    both are required for id and display, and NewsAPI has been known to
    return null-content articles (removed/paywalled) that still carry a
    url and title, so this is a narrower check than "require all fields".
    """

def normalize_batch(raw_articles: list[dict]) -> list[ContentItem]:
    """normalize_article over a list; skips (logs, doesn't raise on) any
    article that fails normalize_article's validation, so one malformed
    article doesn't fail the whole poll."""
```

---

## `entity_match.py`

```python
class EntityAlias(BaseModel):
    symbol: str            # e.g. "NVDA" - matches entities.symbol in the db design
    aliases: list[str]     # e.g. ["Nvidia", "Nvidia Corporation"]

class EntityMatcher:
    def __init__(self, entities: list[EntityAlias]):
        """Pre-builds a case-insensitive lookup once per batch of entities,
        rather than re-scanning the entity list per article."""

    def match(self, title: str, text: str | None) -> list[str]:
        """Case-insensitive substring match of symbol + each alias against
        title + text. Returns the list of matched entity symbols (deduped,
        order-preserving). See parent spec's "Entity/ticker tagging" note
        on false-positive risk for generic-word company names — not
        addressed here by design, left for a later relevance pass."""
```

---

## `service.py`

```python
def poll_news(
    filters: NewsQueryFilters,
    gateway: NewsApiGateway,
    entities: list[EntityAlias],
) -> list[ContentItem]:
    """The one function the rest of the codebase calls. One poll = one call:
      1. gateway.fetch_raw(filters)       -> list[dict] raw articles
      2. normalize.normalize_batch(...)   -> list[ContentItem]
      3. EntityMatcher(entities).match()  -> per item, fills item.entities
      4. return the list

    Does not: schedule itself, retry on NewsApiError, de-dup against a
    previous poll, or filter on content quality — all explicitly out of
    scope per the parent spec.
    """
```

---

## Testing approach

- `test_query_builder.py` — pure unit tests, no network: assert the exact
  `q` string produced for each combination of keyword/phrase/boolean, and
  that an all-empty combination raises.
- `test_normalize.py` — feed `fixtures/sample_article.json` (and a
  deliberately malformed variant) through `normalize_article`.
- `test_entity_match.py` — small hardcoded `EntityAlias` list, assert
  matches/non-matches including the known "Target"-style false positive,
  documented as accepted behavior rather than a bug.
- `test_client.py` — inject a fake `http_client` whose `get_everything`
  returns canned success/error payloads; asserts `NewsApiError.retryable`
  per the mapping table, with zero real HTTP calls.
- `test_service.py` — wires fakes/fixtures through `poll_news` end-to-end,
  asserting the final `ContentItem` list including `entities` tagging.

## Dependencies

- `newsapi-python` (NewsAPI client)
- `pydantic` (models/validation)
- Everything else is stdlib (`datetime`, `enum`).
