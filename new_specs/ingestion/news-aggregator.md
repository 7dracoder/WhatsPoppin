# Ingestion: News Aggregator

**Status:** Draft

See [ingestion overview](./README.md) for the shared content-item schema and
cross-source dependencies.

## Problem / Why

News coverage is a primary signal for [[trending-cards]] and
[[news-graphing]], and a source of co-mention data for
[[company-network]]. A news API with a rich filter set lets the system pull
tightly-scoped, relevant coverage instead of a noisy raw feed.

## Goals

- Poll a news API on a configurable interval and return a list of relevant
  articles, filtered to the configuration below.
- Tag articles that match a tracked ticker or company name, so downstream
  features can tie news to specific entities.

## Non-Goals

- Classifying/scoring articles — that's [[jev-classifier]].
- Deciding which tickers/companies are "tracked" — assumed to come from
  wherever [[company-network]] or the user's watchlist defines that list
  (not yet specified).

## User Stories / Example Interactions

- As a user, I want trending cards backed by real, recent news — not
  opinion pieces, sponsored content, or live-blog noise.
- As the system, when an article matches a tracked ticker, I want it tagged
  so [[company-network]] and [[news-graphing]] can use it.

## Functional Requirements

1. Poll the news API on a configurable `poll interval`, capped at a
   configurable `maximum results per poll`.
2. Support filtering by: source IDs, source category, country, language,
   keyword query, exact-phrase and boolean terms, time window, sort order,
   domain allowlist or exclusion list, and title similarity (de-dup).
3. Support content-quality filters: minimum article length or require full
   text, exclude opinion/sponsored/live-blog content.
4. Support ticker or company-name matching to tag articles with entities.
5. Emit each matched article as a normalized content item (see [ingestion
   overview](./README.md#shared-normalized-content-item-schema)).

## Design / Approach

**Provider:** [NewsAPI.org](https://newsapi.org), via the community-maintained
`newsapi-python` package (`pip install newsapi-python`, imported as
`newsapi.NewsApiClient`). It's an unofficial thin wrapper — it just builds
query params and calls `requests.get` under the hood, raising
`NewsAPIException` on a non-ok response. Using it instead of calling
`requests` directly saves us from hand-building query strings, but it's thin
enough that nothing here is locked in — swapping providers later mainly
means rewriting the query-builder and normalizer below.

**Scope of this spec:** the business-logic layer that turns a filter config
into a NewsAPI call and a list of normalized content items — one poll =
one function call. Explicitly **not** covered here (see Open Questions):
the scheduler that calls it on an interval, cross-poll de-dup, and
content-quality filtering. Those are separate concerns layered on top later.

**Why `/v2/everything`, not `/v2/top-headlines`:** `/everything` is the only
endpoint with keyword/boolean/exact-phrase query syntax, domain
allow/exclude, and an arbitrary `from`/`to` date window — i.e. most of the
filter list below. `/top-headlines` trades that away for `country` and
`category` support, which is why those two filters can't be had "for free"
together with the rest (see Open Questions).

**Collapsing three filters into one query string:** NewsAPI's `/everything`
takes a single `q` string, so "keyword query," "exact-phrase," and "boolean
terms" aren't three separate API params — they're three ways of building
one string, combined here as:

- `keyword_query` → used as-is (bare terms, implicitly OR'd by NewsAPI).
- each `exact_phrases` entry → wrapped in `"..."`.
- `boolean_terms` → a raw `AND`/`OR`/`NOT` + parens expression, supplied
  by the caller in NewsAPI's own syntax.

If more than one is set, the query builder joins them with `AND` (phrases
and boolean terms are usually meant to narrow, not widen, a plain keyword
search).

**Article IDs:** NewsAPI articles don't come with a stable unique ID (no
`id` field on the article object — only `source.id` for the publisher).
The query builder uses the article `url` as `source_native_id` /
`ContentItem.id`, since URLs are unique per article in practice and the
`messages` table already expects a `source_native_id` for de-dup.

**Entity/ticker tagging:** done here, per-poll, per this spec's Non-Goals
(scoring is jev-classifier's job, but *tagging* an article with the
entities it mentions is this aggregator's). Implementation is deliberately
simple: case-insensitive substring match of each tracked entity's symbol
and known aliases (e.g. `"NVDA"`, `"Nvidia"`) against the article's title +
text. This will produce false positives on generic-word company names
(e.g. "Target") — acceptable for now since jev-classifier (or a later
pass) can refine relevance; `message_entities.relevant` in the db design
already has a slot for exactly that kind of correction.

**Proposed module layout** (none of this exists yet — greenfield):

```
ingestion/news_api/
  models.py          # NewsQueryFilters, ContentItem
  query_builder.py    # NewsQueryFilters -> newsapi-python call kwargs
  client.py           # NewsApiGateway: wraps NewsApiClient, raises NewsApiError
  normalize.py        # raw article dict -> ContentItem
  entity_match.py      # substring/alias matcher -> list[str] of entity symbols
```

`client.py` wraps `NewsApiClient.get_everything(**kwargs)` and re-raises its
`NewsAPIException` as our own `NewsApiError(code, message, retryable: bool)`
— e.g. `rateLimited` → retryable, `apiKeyInvalid` → not. No retry/backoff
loop is implemented here; `retryable` is just the signal a future scheduler
would need to act on it.

## Interfaces / Data Model

Query-side filter config (maps onto `/v2/everything` params):

```python
@dataclass
class NewsQueryFilters:
    source_ids: list[str] | None = None    # NewsAPI "sources"; max 20 IDs/call
    keyword_query: str | None = None        # "q" bare terms
    exact_phrases: list[str] | None = None  # each wrapped in quotes into "q"
    boolean_terms: str | None = None        # raw AND/OR/NOT(...) expression into "q"
    search_in: list[str] | None = None      # subset of title/description/content
    domains: list[str] | None = None
    exclude_domains: list[str] | None = None
    language: str = "en"                    # ISO 639-1
    from_time: datetime | None = None
    to_time: datetime | None = None
    sort_by: Literal["relevancy", "popularity", "publishedAt"] = "publishedAt"
    page_size: int = 100                    # "max results per poll"; NewsAPI caps at 100
    page: int = 1
```

Normalized output (fits the [shared content-item schema](./README.md#shared-normalized-content-item-schema)):

```python
@dataclass
class ContentItem:
    source: Literal["news"] = "news"
    id: str              # = article url, see "Article IDs" above
    author: str | None   # NewsAPI's source.name (outlet), not a byline
    title: str
    text: str | None     # NewsAPI "content" field — truncated ~200 chars on free tier
    entities: list[str]  # tracked symbols matched by entity_match.py
    url: str
    published_at: datetime
```

**Deliberately excluded from `NewsQueryFilters`** (see Open Questions for
why): `country`, `source_category`, `title_similarity_threshold`,
`min_article_length`, `require_full_text`, `exclude_content_types`,
`poll_interval`.

## Dependencies

- A news API (provider TBD).
- [[jev-classifier]] — downstream consumer of emitted items.

## Open Questions

**Resolved:**

- **Provider:** NewsAPI.org, via `newsapi-python`. Free "Developer" tier:
  100 requests/day, ~1 month lookback, articles delayed ~24h, `content`
  field truncated to ~200 chars, and **dev/testing use only — the free
  tier's terms forbid production/commercial use.** Fine for the MVP
  demo; would need a paid plan (Business+, $449/mo) before shipping this
  for real.
- **Country / source category:** not supported by `/v2/everything` (only
  `/v2/top-headlines` and `/v2/sources` have them). **Dropped for MVP** —
  not implemented in `NewsQueryFilters`. If needed later, the options are:
  resolve country+category to source IDs via one `/v2/sources` call and
  feed those into `sources` (capped at 20 IDs/call), or run a separate
  `/top-headlines` poll alongside `/everything`.
- **Min article length / require full text / exclude opinion-sponsored-live-blog:**
  marked **not implemented**. NewsAPI has no content-type flag for these,
  and the free tier's 200-char truncation makes "require full text"
  infeasible regardless. Revisit only if/when a paid plan is in use.
- **Title-similarity de-dup:** **out of scope for this layer** — each poll
  emits every matched article as-is; de-duplication across polls is
  [[jev-classifier]]'s job, not the aggregator's.
- **Poll interval / scheduling:** **out of scope for this spec.** This
  covers the single-poll business logic only (filters in → normalized
  items out); what calls that on a timer (APScheduler, cron, etc.) is a
  separate, not-yet-designed piece.
- **Full-text storage vs. metadata + link only:** moot for now — NewsAPI's
  free tier only ever returns a ~200-char `content` snippet, so there's no
  full text to choose between storing or not.

**Still open:**

- Where does the "tracked ticker/company" list (`entity_match.py`'s input)
  come from — a DB table, a static config, the `entities` table in
  [mock_db_design](../../mock_db_design/db-design.md)?
- Shared rate-limit/backoff strategy across ingestion workers, or fully
  independent per source (ties into the not-yet-designed scheduler)?

## Acceptance Criteria

- Given a `NewsQueryFilters` config, a single call returns a list of
  `ContentItem`s matching that config, each tagged with any tracked
  entities it mentions.
- Calling with `source_ids` over 20 entries, or with an invalid API key,
  raises `NewsApiError` rather than an unhandled exception.
