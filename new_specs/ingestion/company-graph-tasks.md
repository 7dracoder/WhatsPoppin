# Company Graph page: feature breakdown (v4)

As of 2026-10-10. v4 records that F0 to F10 are built and running on real data (SEC filings, Claude, NewsAPI, Alpaca), the option to call Claude directly, and what the first live runs showed. Only F11 (demo tools) is left.

## What the page does

The user searches for any listed company. The page shows a graph of the companies it does business with (links), and highlights the linked companies that a recent event may affect (highlights). Links come from SEC filings and are stored for days. Highlights come from news and from prediction market alerts in the last 7 days, and are refreshed at most every `GRAPH_NEWS_TTL_HOURS` per company.

This feature replaces the deleted `new_specs/company-network.md` and answers its open question: the graph is built from supply-chain and competitor relationships stated in SEC filings, with a same-industry fallback.

## Status

| Feature | Status |
| --- | --- |
| F0 Contracts, models and fixtures | Done |
| F1 Company directory | Done |
| F2 SEC client | Done |
| F3 Filing trimmer | Done |
| F4 Relationship extractor | Done |
| F5 Link finder | Done, verified live |
| F6 News events | Done |
| F7 Prediction market events | Done |
| F8 Highlight builder | Done, verified live |
| F9 Graph API | Done, verified live |
| F10 Graph page | Done, works against the real API |
| F11 Demo tools | Not started, unblocked |
| F12 X posts as events | Done, verified live (`company_graph/social_events.py`) |
| F13 News about a linked pair | Done, verified live (`company_graph/pair_news.py`, `GET /graph/{ticker}/news/{other}`, the page's company panel) |
| F14 Relationship tags (Haiku) | Not started, unblocked |

## Live results (2026-10-10)

Run locally against SEC, Claude (Anthropic API), NewsAPI and Alpaca, with a local Postgres:

| Company | Links found | Notes |
| --- | --- | --- |
| NVDA | TSMC, Micron, SK Hynix, Fabrinet (suppliers); CoreWeave, Nebius, IREN (customers); Intel, Microsoft (partners) | 4 news highlights (TSMC, SK Hynix, Micron, Intel) with price changes |
| TSM (20-F filer) | AMD, NVIDIA, Qualcomm, Broadcom, Analog Devices (customers); Lam Research, KLA (suppliers) | |
| LLY | McKesson, Cencora, Cardinal Health (distributors); Incyte, AbCellera (partners); Novo Nordisk, Viking (competitors) | Needed the short-name fix below |
| TSLA | CATL (supplier); Uber, Aurora, XPeng (competitors); GM, Toyota, Ferrari (industry peers) | Panasonic does not file with SEC, so it cannot appear |
| AAPL | MP Materials (supplier), TD SYNNEX (distributor), app partners | Apple's 10-K names no suppliers, so links come from other companies' filings |
| JPM | Bank of America, Citi, Capital One (industry peers) and 2 depositary relationships | Banks are thin: filings do not describe banking relationships the way they describe suppliers |

Fixed during the live runs: SEC full-text search 500s are retried; reverse lookups read the largest companies first (one filing per company); industry peers are added on every run; the extractor prompt excludes landlords, lenders, lawsuits and hypotheticals; 215 SEC names produced broken search phrases ("JPMORGAN CHASE &", "CORP/OH/"); a run whose filings all failed is an error even when peers were saved.

**Known limits:**
- The page colours linked companies only. A highlight whose target is the searched company (news about a linked company that affects it) is stored but not shown on that company's own graph; it shows on the linked company's graph. Colouring the centre node would fix this.
- Suppliers that do not file with SEC (Panasonic, CATL's own filings, private companies) only appear when another filing names them.
- NewsAPI's free tier delays articles by about a day and includes small sites. Market highlights need the `alerts` table filled by the ingestion workers and alert detector.
- X posts (F12) are noisier than news. Jev keeps only posts that state an event about the named company at 0.9 probability or more; in the first live run 1 of 21 posts for NVDA's graph was kept (GlobalFoundries' $2B agreement with TSMC). Each X search reads up to 100 posts, which X bills for, so it has its own daily budget.
- The API keeps its run registries in memory: run one API process.

## What already exists (use it, do not rebuild it)

| Existing piece | Where | How this feature uses it |
| --- | --- | --- |
| Kalshi and Polymarket ingestion | `backend/ingestion/kalshi/`, `backend/ingestion/polymarket/`, `backend/ingestion/polymarket_us/`, `backend/ingestion/common/` | Read the `markets` table. Never write to it. |
| Alert detector | `alert_detector/`, model `backend/models/alert.py`, table `alerts` | Read `alerts` rows as "a market moved" events. Never update `status` or `claimed_at`: the planned enricher claims alerts through those columns. |
| SQLAlchemy base and engine | `backend/models/base.py`, `backend.ingestion.common.db.make_engine` and `connect` | Import them. Tables are created with `Base.metadata.create_all`; there are no migrations. The database code is async (asyncpg). |
| LLM client | `backend/llm/client.py`, `complete_structured(system, user, response_model, model=DEFAULT_MODEL)` | Every free-form model call (F4, F8). It calls the Nicrron gateway with `NICRRON_API_KEY` and parses the reply into a Pydantic model. Raises `LlmError`. Synchronous. |
| Jev classifier | `backend/classification/`, `classify(title, text, spec)` with `ChoiceSpec`, `BooleanSpec` and others; `is_relevant` and `filter_relevant` in `relevance.py` | F6's event typing and relevance filtering. Uses the same `NICRRON_API_KEY`. Raises `JevError`. Synchronous. |
| Entity matching | `backend/entities/`, `EntityAlias`, `EntityMatcher` | F1's `aliases_for` returns `EntityAlias` objects so news can be tagged by company. |
| News fetch | `backend/ingestion/news_api/`, `poll_news(filters, gateway, entities)`, `NewsQueryFilters`, `NewsApiGateway` | Call `poll_news`. It is synchronous, does not store anything, does not deduplicate, and does not classify. |
| Stock prices | `backend/ingestion/alpaca/`, `fetch_price_series(ticker, tier, gateway)` with `ZoomTier` and `AlpacaApiGateway(key_id, secret_key)` | F8's `price_change_pct`. Needs `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`. Free tier delays the last 15 minutes. |
| Twitter lookup | `backend/ingestion/twitter_lookup/` | Not used in v1. |
| Frontend shell | `frontend/` (Vite, React 18, TypeScript, Tailwind, react-router), `frontend/src/lib/apiClient.ts` | F10 added a page and a route. Fetch with `get<T>()`. |
| Specs and database design | `new_specs/`, `mock_db_design/db-design.md`, `architecture/tech-stack.md` | Follow them. The shared table names below come from `db-design.md`. |

**Built by this feature** (import these; do not rebuild them):

| Module | What to use |
| --- | --- |
| `company_graph/companies.py` (F1) | `resolve(text)`, `search_companies(prefix)`, `aliases_for(symbols)`, `async ensure_entity(session, company)`, `Company(symbol, name, cik)` |
| `company_graph/sec.py` (F2) | `SecClient` (async; `list_filings`, `fetch_text`, `full_text_search`, `get_json`), `filing_url`. Each client has its own 5-per-second limiter, so one run must share one client. |
| `company_graph/trim.py` (F3) | `trim_by_phrases(text)`, `trim_by_name(text, name_variants)`, returning `Chunk(offset, text)` |
| `company_graph/config.py` (F0) | `load() -> Config` with every setting below |
| `company_graph/db.py` (F0) | `connect`, `make_engine`, `create_tables`, `MODELS`, `TABLES` |
| `company_graph/schemas.py` (F0) | The API response models (`GraphResponse` and its parts), the allowed values (`RELATIONSHIP_TYPES`, `EVENT_TYPES`, `NEWS_EVENT_TYPES`, `DIRECTIONS`), `CONFIDENCE`, `DEFAULT_WEIGHT`, `load_fixture(ticker)` |
| `backend/models/` (F0) | `Entity`, `EntityRelationship`, `MarketEntity`, `GraphCompanyProfile`, `GraphLinkRun`, `GraphProcessedFiling`, `GraphEvent`, `GraphHighlight`, one file each |

## What does not exist yet

- Redis, pub/sub, or a WebSocket server. The page polls instead.
- The `messages` and `message_entities` tables from `db-design.md`. This feature does not need them.
- `contracts/`, migrations.

## Design decisions

| Decision | Blocks | Status |
| --- | --- | --- |
| Who creates the FastAPI app, and at what path? | F9 | Resolved. The app is `backend/main.py` (run with `uv run uvicorn backend.main:app --reload`), with one router per area in `backend/api/` (see `new_specs/fastapi.md`). CORS allows the Vite dev server. |
| Where does the `entities` model come from? | F0 | Resolved: F0 added it. |
| May `entity_relationships` gain `summary` and `evidence_url`? | F0 | Resolved: added, and `db-design.md` updated. |
| Which LLM provider? Who builds Jev? | F4, F6, F8 | Resolved: the Nicrron gateway through `backend/llm`; Jev is `backend/classification`. |
| Which stock price source? | F8 | Resolved: Alpaca, `backend/ingestion/alpaca`. |
| What happened to `company-network.md`? | Nothing now | Resolved: that spec was deleted; this file replaces it. |
| May `entity_relationships` gain `tags`? | F14 | Resolved: added (`TEXT[]`), `db-design.md` updated, column added on the shared database via a manual `ALTER TABLE` (no migrations, so `create_all` won't add it to a table that already exists). |

## Implementation rules

- Follow the repo where it disagrees with this file, and say so.
- **Write only here:** `company_graph/`, `tests/company_graph/`, new model files in `backend/models/` (never existing ones), `frontend/src/features/company-graph/`, `frontend/src/pages/CompanyGraphPage.tsx`, `frontend/src/types/graph.ts`, and this file.
- **Never edit:** `backend/ingestion/`, `backend/llm/`, `backend/classification/`, `backend/entities/`, `alert_detector/`, `baselines/`, any existing file in `backend/models/` (including `__init__.py`), any existing test, or any other existing frontend file.
- **Never write to these tables:** `alerts`, `markets`, `market_prices`, `market_trades`, `market_hourly`, `market_baselines`.
- **Never run** `python -m backend.ingestion.reset_db`. It drops other modules' tables. No table of this feature may declare a foreign key to `markets` or `alerts`, because `reset_db` drops them with `CASCADE`.
- Match the house style of `alert_detector/`: settings come from `company_graph/config.py`, database access from `company_graph/db.py`, pure functions with no I/O where possible, `python -m company_graph` as the entry point, and `company_graph/company_graph.md` kept up to date.
- The shared LLM, Jev, news and Alpaca functions are synchronous. Call them from async code through `asyncio.to_thread`.
- Run commands with `uv run`. Tests go in `tests/company_graph/` and run with `uv run pytest tests/company_graph`. Tests never call the network, a real model or a real database: fake the model call, use `httpx.MockTransport` for HTTP, and SQLite for tables. pytest-asyncio is not installed, so drive async code with `asyncio.run`.
- Every link and every highlight carries a source URL that came from a real fetch in that run. Never save one from model memory.
- State relationships and events factually. No wording predicts a price or suggests a trade.

## Shared contracts

All of these exist. The models are in `backend/models/`, and the allowed values are constants in `company_graph/schemas.py`.

**Tables from `db-design.md`**

| Table | Columns |
| --- | --- |
| `entities` | `symbol` (pk), `name`, `type` (`company`, `person`, `industry`), `created_at` |
| `entity_relationships` | `id` (UUID), `entity_symbol` and `related_entity_symbol` (both foreign keys to `entities`), `relationship_type`, `weight`, `confidence`, `source`, `last_confirmed_at`, `summary`, `evidence_url`, `tags` (`TEXT[]`, nullable, F14); unique on (`entity_symbol`, `related_entity_symbol`, `relationship_type`) |
| `market_entities` | `source`, `market_id`, `entity_symbol` (composite pk; no foreign key to `markets`) |

- `relationship_type` is one of `supplier`, `customer`, `partner`, `competitor`, `sector_peer`. It describes the related company's role: `supplier` means the related company supplies the entity.
- `source` is `filing` or `sector`. `confidence` is 0.9 for `filing` and 0.3 for `sector`, and `weight` is 1. These numbers are placeholders.
- A related company with no US ticker gets an `entities` row whose `symbol` is its name, as `db-design.md` already does for people and industries.

**Tables private to this feature** (prefixed `graph_`)

| Table | Columns |
| --- | --- |
| `graph_company_profiles` | `symbol` (pk), `cik`, `sic_code`, `sic_description`, `listing_venue` |
| `graph_link_runs` | `symbol` (pk), `status` (`running`, `done`, `error`), `fetched_at`, `error` |
| `graph_processed_filings` | `accession_number` (pk), `cik`, `form`, `processed_at` |
| `graph_events` | `id`, `entity_symbol`, `source` (`news`, `market`), `event_type`, `title`, `url`, `occurred_at`, `alert_id` (nullable, no foreign key); unique on (`entity_symbol`, `url`) |
| `graph_highlights` | `id`, `event_id` (foreign key to `graph_events`, cascades on delete), `source_symbol`, `target_symbol`, `direction` (`may_benefit`, `may_face_pressure`), `reason`, `source_url`, `event_time`, `price_change_pct` (nullable), `created_at` |

`event_type` is one of `product_launch`, `contract`, `earnings_surprise`, `recall`, `acquisition`, `odds_move`.

The result is called a highlight, not a signal, because `alert_detector/signals.py` already uses "signal" for something else.

**API** (REST, polled by the page; no WebSocket). The shape is `GraphResponse` in `company_graph/schemas.py`, mirrored by `frontend/src/types/graph.ts`.

`GET /graph/{ticker}` returns:

```json
{
  "company": {"symbol": "TSLA", "name": "Tesla, Inc."},
  "status": "running | done | error",
  "nodes": [{"symbol": "XYZ", "name": "Example Corp", "type": "supplier"}],
  "links": [{"source": "TSLA", "target": "XYZ", "type": "supplier", "summary": "...", "evidence_url": "..."}],
  "highlights": [{"target": "XYZ", "direction": "may_benefit", "event_type": "product_launch", "reason": "...", "source_url": "...", "event_time": "...", "price_change_pct": null}]
}
```

`nodes` does not include the searched company; the page adds it from `company`.

`GET /companies/search?q=` returns up to 10 `{symbol, name}` matches.

**Config** (read by `company_graph/config.py`; all in `.env.example`; defaults are placeholders to tune)

| Variable | Default | Meaning |
| --- | --- | --- |
| `GRAPH_LINK_TTL_DAYS` | 7 | How long stored links are reused before a rebuild |
| `GRAPH_EVENT_WINDOW_DAYS` | 7 | How far back highlights look |
| `GRAPH_MAX_LINKED` | 12 | Most linked companies per graph |
| `GRAPH_NEWS_TTL_HOURS` | 6 | How long one company's news result is reused |
| `GRAPH_NEWS_DAILY_BUDGET` | 40 | Most NewsAPI requests this feature may make per day (the account-wide limit is 100) |
| `GRAPH_FAKE` | 0 | When 1, serve fixture data and call no outside service |
| `GRAPH_LLM_PROVIDER` | `nicrron` | `nicrron` (the shared client) or `anthropic` (Claude directly, through the Anthropic SDK) for F4, F6 typing and F8 |
| `GRAPH_LLM_MODEL` | `backend/llm/client.py`'s `DEFAULT_MODEL` (`nicrron/auto`) | Nicrron model id |
| `GRAPH_ANTHROPIC_MODEL` | `claude-opus-5-5` | Claude model id when `GRAPH_LLM_PROVIDER=anthropic`; `claude-haiku-5-5` costs about 1/40th |
| `ANTHROPIC_API_KEY` | none | Read by the Anthropic SDK when `GRAPH_LLM_PROVIDER=anthropic` |
| `SEC_CONTACT_EMAIL` | none | Sent in the SEC User-Agent header; required for any SEC call |
| `NEWSAPI_KEY` | none | NewsAPI key, shared with the news ingestion |
| `DATABASE_URL` | none | The shared database |
| `NICRRON_API_KEY` | none | Read by `backend/llm` and Jev directly, not by `config.py`; needed when the provider is `nicrron` |
| `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` | none | Optional; read by F8 for the price change (null without them) |

## Build order

| Step | Feature | Waits on |
| --- | --- | --- |
| Done | F0 to F10, F12 | |
| 1 | F11 Demo tools | Nothing |
| 1 | F14 Relationship tags (Haiku) | Nothing (reads `entity_relationships` rows F4/F5 already saved) |

Suggested next, outside the original plan: colour the searched company when a highlight targets it (frontend), fit the graph to its box on load (frontend), and deploy (see the team's hosting plan).

---

## F0. Contracts, models and fixtures

**Status:** done. **Touches the shared design:** yes, the three shared tables.

**Build:**
- `backend/models/entity.py`, `backend/models/entity_relationship.py`, `backend/models/market_entity.py`, and one file per `graph_` table, each subclassing `Base` from `backend.models.base`.
- Do not edit `backend/models/__init__.py`. `company_graph/db.py` imports these model modules directly, then calls `Base.metadata.create_all`, so the tables are created without touching a shared file.
- `company_graph/config.py`, following the `alert_detector/config.py` pattern.
- `company_graph/fixtures/` with a full `GET /graph/{ticker}` response for three tickers, each with 6 to 10 links and 1 to 3 highlights.
- `company_graph/company_graph.md` describing how to run the package.

**As built:** also `company_graph/schemas.py` (the API response models, allowed values and `load_fixture`). The fixtures are identical to the frontend's, and a test fails if they drift. Table tests run on SQLite and compile the Postgres DDL; there was no Postgres to test against.

**Done when:** `create_all` builds every table on an empty database and on a database that already has the market tables, and each fixture validates against the API shape.

## F1. Company directory and ticker resolver

**Status:** done. **File:** `company_graph/companies.py`

**Build:**
- Load SEC's `company_tickers.json` once and keep it in memory. Cache the file on disk under `.cache/company_graph/` (`.cache` is already in `.gitignore`).
- `resolve(text) -> company | None`: exact ticker first, then a normalized name match that ignores suffixes such as Inc, Corp and Ltd. Return `None` when the match is not confident.
- `search_companies(prefix, limit=10)`.
- `ensure_entity(session, company)` (async): upsert one `entities` row with `type = "company"`. Call it only for companies that are searched or linked. Do not bulk-load every listed company into the shared `entities` table.
- `aliases_for(symbols) -> list[EntityAlias]`, using `EntityAlias` from `backend.entities`, so the news feature can tag articles.

**As built:** the ticker list is downloaded through F2's `SecClient.get_json` and cached for 7 days. If several companies match a name, `resolve` returns `None`.

**Done when:** `resolve("TSLA")` and `resolve("Tesla Inc")` return the same company, `resolve("Customer A")` returns `None`, and a search returns in under 100 ms.

## F2. SEC client

**Status:** done. **File:** `company_graph/sec.py`

**Build:**
- An `httpx` async client (`httpx` is already a dependency) that sends `User-Agent` with `SEC_CONTACT_EMAIL` on every request and shares one rate limiter set to 5 requests per second.
- `list_filings(cik, forms, since)` from `https://data.sec.gov/submissions/CIK{10-digit cik}.json`. Also return the company's SIC code and description if the response includes them, for `graph_company_profiles`.
- `fetch_text(url)`: download a filing document and strip HTML to text with the standard library's `html.parser`, so no new dependency is needed.
- `full_text_search(query, forms, since, limit)` from `https://efts.sec.gov/LATEST/search-index`. This endpoint is undocumented, so confirm its current parameters and response shape before coding against it.
- Retry with backoff on 429 and 503. Cache responses on disk under `.cache/company_graph/` by URL.

**As built:** the EFTS endpoint's parameters and response shape, confirmed live, are in the module docstring. `list_filings` reads only the submissions file's recent block (at least the last year). Full-text search returns one hit per document, so a filing and its exhibit can both appear. The rate limiter belongs to each client, so a run must share one `SecClient`.

**Done when:** `list_filings` returns the latest 10-K for a known company, 50 calls in a burst never exceed the rate limit, and a call without `SEC_CONTACT_EMAIL` fails with a clear error before any network request.

## F3. Filing trimmer

**Status:** done. **File:** `company_graph/trim.py`

**Build:** two pure functions with no network or model calls.
- `trim_by_phrases(text, phrases, radius=1500)`. Default phrases: "accounted for", "% of revenue", "% of net sales", "sole source", "single source", "supplier", "competitors", "compete with", "agreement with".
- `trim_by_name(text, name_variants, radius=1500)`.
- Both merge overlapping windows, cap the result at 12 chunks, and return each chunk with its character offset.

**As built:** past 12 chunks, the 12 with the most hits are kept, in document order. Names match as whole words; phrases match as substrings, ignoring case.

**Done when:** tests cover overlapping hits, no hits and the cap, and a 300-page filing is trimmed in under one second.

## F4. Relationship extractor

**Status:** done. **Files:** `company_graph/llm.py`, `company_graph/extract.py`

**As built:** `llm.complete` uses the shared Nicrron client by default, or Claude directly through the Anthropic SDK with `GRAPH_LLM_PROVIDER=anthropic` (structured outputs into the Pydantic model, low effort, server-side fallbacks where the model supports them). Failures are `LlmError` either way.

**Build:**
- `llm.py`: a thin wrapper, `complete(system, user, response_model)`, over the shared `backend.llm.client.complete_structured` with `model` set from `GRAPH_LLM_MODEL`, so the model can be swapped in one place. Every free-form model call in this feature (F4, F8) goes through it. It is synchronous; call it through `asyncio.to_thread`.
- `extract(chunk, filer, subject) -> [relationship]`, with a Pydantic response model whose types come from `schemas.RELATIONSHIP_TYPES`. The prompt asks what business relationship between the filer and the subject the text states, and says to return an empty list when it states none.
- `save_relationship(...)`: validate, call `ensure_entity` for both companies, and upsert into `entity_relationships`. Reject when the type is not allowed, the evidence URL is missing or was not fetched in this run, or the related company is the entity itself.

**Done when:** a set of 10 hand-written chunks (5 with a relationship, 5 passing mentions) scores at least 9 correct with the model call faked in tests, and each rejection rule has a test.

## F5. Link finder

**Status:** done, verified live on TSLA, AAPL, NVDA, TSM, JPM and LLY (10 to 25 seconds each). **File:** `company_graph/links.py`

**As built:** links are stored from the filer's side, so read them with `read_links`, which flips reverse-lookup rows. Subjects in a company's own filings are found with F7's `find_companies`, so non-US counterparties named only there (such as CATL) are missed. 8-Ks are fetched and kept only if they show Item 1.01 or 2.01. After the first live runs: reverse hits keep one filing per company and read the largest companies first; the 3 largest same-industry companies are always added as `sector_peer` (from SEC's company list by SIC code); annual reports include 20-F and 40-F; SEC 5xx errors are retried.

**Build:** `build_links(symbol)` that
1. Marks `graph_link_runs` as `running`. Skips the run when the last one is `done` and newer than `GRAPH_LINK_TTL_DAYS`.
2. Own filing: latest 10-K, `trim_by_phrases`, `extract`.
3. Reverse lookup: `full_text_search` for the company name in quotes, 10-Ks from the last 18 months, top 10 hits from other filers, `trim_by_name`, `extract`.
4. Recent 8-Ks from the last 90 days that announce agreements or acquisitions, same trim and extract.
5. Skips any filing already in `graph_processed_filings`, and records each one it reads.
6. Keeps at most `GRAPH_MAX_LINKED` links, preferring suppliers and customers.
7. If no links are found, saves `sector_peer` links to companies in `graph_company_profiles` with the same SIC code. Only companies already profiled can match, so this fallback is often empty and the page must handle that.
8. Updates `graph_link_runs` to `done` or `error`.

Use one `SecClient` for the whole run, so its rate limiter covers every request. Run documents in parallel with `asyncio`. Save each company's SIC code from `list_filings` into `graph_company_profiles`. Stop after 60 seconds and keep what was saved. Links from deal news and a web-search fallback are out of scope for v1.

**Done when:** a run for a large US company saves at least 3 links with working filing URLs, a second run inside the TTL makes no network calls, and no filing is read twice.

## F6. News events

**Status:** done. Fetching (`poll_news`) and classification (Jev) exist; this feature connects them and stores the result. With `GRAPH_LLM_PROVIDER=anthropic`, events are typed with Claude through `llm.complete` instead of Jev, using the same labels. **File:** `company_graph/news_events.py`

NewsAPI limits, from `new_specs/ingestion/news-aggregator.md`: the free tier allows 100 requests per day per API key, articles arrive about 24 hours late, and article text is cut to about 200 characters.

**Build:**
- `fetch_news_events(symbols)`: make **one** `poll_news` call for the whole graph, with the company names joined by `OR` in `boolean_terms` and `from_time` set to `GRAPH_EVENT_WINDOW_DAYS` ago. Confirm NewsAPI's query length limit and split into a second call only if needed. `poll_news` is synchronous, so call it through `asyncio.to_thread`.
- Pass `aliases_for(symbols)` so each article comes back tagged with the companies it mentions.
- Reuse a stored result for `GRAPH_NEWS_TTL_HOURS`, and stop calling NewsAPI once `GRAPH_NEWS_DAILY_BUDGET` requests have been made that day.
- Deduplicate by URL and by normalized title.
- `classify_event(item) -> event_type | None`, through Jev: `backend.classification.classify(title, text, ChoiceSpec(...))`, with one label per type in `schemas.NEWS_EVENT_TYPES` plus a `none` label for opinion pieces, roundups and anything else. `none` maps to `None`. Jev is synchronous; call it through `asyncio.to_thread`. Optionally drop passing mentions first with `backend.classification.relevance.filter_relevant`.
- Save each typed story once into `graph_events` with `source = "news"`.

**Done when:** one search makes at most one NewsAPI request, two articles on the same story produce one event, an opinion piece produces none, and a fixture set of 10 headlines is tagged with at least 8 correct with the Jev call faked.

## F7. Prediction market events

**Status:** done. Detection exists (Donn's alert detector); mapping alerts to companies is new. **File:** `company_graph/market_events.py`

Do not compare odds yourself. `market_prices` keeps only 30 minutes of rows, and the alert detector already decides what counts as a real move.

**Build:**
- `fetch_market_events(symbols)`: read `alerts` rows (model `backend.models.alert.Alert`; markets in `backend.models.market.Market`) created in the last `GRAPH_EVENT_WINDOW_DAYS`. This is read-only.
- For each alert, take the market title and event title from `alerts.context` and the `markets` row, and run `resolve` over the company names found in them. Save each match in `market_entities`.
- Save one `graph_events` row per matched company, with `source = "market"`, `event_type = "odds_move"`, `title` set to the alert's `summary`, `url` set to the market's URL, and `alert_id` set.

**Done when:** a fixture alert whose market title names a listed company produces one event, an alert naming no listed company produces none, and no test or code path updates the `alerts` table.

**Known limit:** `.env.example` follows the Kalshi series `KXHIGHNY` and `KXBTCD` and the Polymarket tags `politics,economy,finance,tech,geopolitics`. Few of those markets name a company, so market-driven highlights will be rare unless Donn adds company-related tags or series to his `.env`.

## F8. Highlight builder

**Status:** done, verified live. **File:** `company_graph/highlights.py`

**As built:** `build_highlights` refreshes news (F6, skipped without `NEWSAPI_KEY`) and market events (F7) for the company and its linked companies, then makes one model call per new event (at most 40 per run, 4 at a time). An event about the searched company is offered with all its linked companies; an event about a linked company targets the searched company. Sector peers get a highlight (`may_face_pressure`) only when the model reports a clear competitive gain. Reasons that read like a forecast or trade advice are dropped. Events already judged are remembered in `.cache/company_graph/highlight_evals.json`, so they cost nothing on the next run. Price change comes from Alpaca, null on any problem.

**Build:** `build_highlights(symbol)` that
1. Loads the company's links, then the `graph_events` rows from the last `GRAPH_EVENT_WINDOW_DAYS` for the company and each linked company.
2. For each event, asks the model through `llm.py` which linked companies the event involves and why, in one sentence each. Events flow both ways along a link.
3. Sets the direction from the relationship type: supplier, customer and partner are `may_benefit`, competitor is `may_face_pressure`.
4. Fills `price_change_pct` with the target's percent change from just before `event_time` to the latest price, using `backend.ingestion.alpaca.fetch_price_series` (the smallest `ZoomTier` whose range covers `event_time`), through `asyncio.to_thread`. Leaves it null when the Alpaca keys are missing, the call fails, or the symbol has no US ticker. It is a past fact; never word it as a forecast.
5. Saves each highlight. Drops any highlight without a source URL.

**Done when:** with fixture links and one fixture launch event, only the relevant neighbors get a highlight, a competitor gets `may_face_pressure`, a run with no events saves nothing and makes no model call, and a failed price call leaves `price_change_pct` null without failing the run.

## F9. Graph API

**Status:** done, verified live; mounted in `backend/main.py`. **File:** `company_graph/api.py`

**As built:** status is `running` while links are being built, and again while highlights are being built (the page keeps polling), then `done`. A failed link run reports `error` with whatever links exist and retries after 5 minutes; a failed highlight run never turns a good graph into an error. Highlight runs happen at most once per `GRAPH_NEWS_TTL_HOURS` per company. Unknown tickers return 404; a missing `DATABASE_URL` returns 503. One process only: the run registries are in memory.

**Build:**
- `router = APIRouter()` with `GET /graph/{ticker}` and `GET /companies/search`.
- `GET /graph/{ticker}`: return stored links at once. When links are missing or stale, start `build_links` as an in-process `asyncio` task and return `status: "running"`. When links are ready, run `build_highlights` and return `status: "done"`.
- Return `schemas.GraphResponse`, so the shape always matches the page.
- With `GRAPH_FAKE=1`, serve `schemas.load_fixture(ticker)` (an empty `done` graph for unknown tickers) and call nothing else.
- Mount it with one `app.include_router(...)` line in `backend/main.py`. CORS for the frontend (`http://localhost:5173`) is already set there. `fastapi` is already a dependency.

**Done when:** tests using FastAPI's test client, with the router mounted on a throwaway app, show that a first request returns `running`, a later one returns `done` with links, and fake mode works with no database and no network.

## F10. Graph page

**Status:** done; works against the real API and in fake mode. New files inside the frontend, plus three small edits to existing files.

**New files:** `frontend/src/pages/CompanyGraphPage.tsx`, `frontend/src/features/company-graph/` (components and fixtures), `frontend/src/types/graph.ts`.

**Small edits to shared files:** a route in `frontend/src/router.tsx`, a nav link in `frontend/src/layout/AppLayout.tsx`, and one graph library (`react-force-graph-2d` or `cytoscape`) in `frontend/package.json`.

**As built:** uses `react-force-graph-2d`. The route loads the page lazily, as its own chunk. The nav link changed two lines of `AppLayout.tsx`, because the import also changed. `VITE_GRAPH_FAKE` is documented in `frontend/.env.example`.

**Build:**
- A search box with autocomplete from `GET /companies/search`.
- A graph view: the searched company in the middle, linked companies around it, and the link type on each edge.
- Highlight styling: one color for `may_benefit`, a second for `may_face_pressure`, and a legend.
- A node card with the event, the reason, the source link, and the price change when it is not null.
- While `status` is `running`, poll `GET /graph/{ticker}` every 1.5 seconds with `get<T>()` from `lib/apiClient.ts`, and draw new nodes as they appear.
- Show an empty state when there are no links, and label sector peers as peers.
- The label reads "exposed to this event".
- With `VITE_GRAPH_FAKE=1`, read the fixtures in `features/company-graph/fixtures/`, so the page works before any backend exists.

**Done when:** in fake mode a search draws the graph, clicking a highlighted node opens its card, `npm run build` and `npm run lint` pass, and the page works at phone width.

## F11. Demo tools

**Status:** not started, unblocked. **File:** `company_graph/__main__.py`

**Build:**
- `uv run python -m company_graph build TICKER`: one `build_links` run, printed.
- `uv run python -m company_graph prewarm TICKER...`: `build_links` for each ticker, waiting for completion.
- `uv run python -m company_graph replay TICKER EVENT_FILE`: insert a recorded event into `graph_events` (never into `alerts`) and run `build_highlights`, so a node lights up on the next poll.

**Done when:** after prewarming, a search for those tickers returns links in under one second, and a replay highlights a node on an open page within 5 seconds.

## F14. Relationship tags (Haiku)

**Status:** not started, unblocked. **Touches the shared design:** yes — `entity_relationships` gains `tags` (`TEXT[]`, nullable). Already resolved and applied: column added on the shared database, `db-design.md` updated, model updated (`backend/models/entity_relationship.py`). **File:** `company_graph/tags.py`

**Why:** F4/F5 already write a factual `summary` for every edge (e.g. "NVIDIA states it buys memory from SK hynix, which would supply memory for the N1X-powered laptop launch"). This feature turns that sentence into a short list of keyword tags (`memory`, `semiconductors`) that a later, separate feature can compare against Kalshi market titles/categories to see whether a market (e.g. one about the Strait of Hormuz) is relevant to a company through an indirect dependency like oil. **That comparison step is not part of this feature** — this is tag generation only.

**Build:**
- A Pydantic response model, e.g. `RelationshipTags(tags: list[str])`.
- `generate_tags(summary: str) -> list[str]`: one call through `company_graph/llm.py`'s existing `complete(system, user, response_model)` (reuse it as-is — it already supports Haiku directly via `GRAPH_LLM_PROVIDER=anthropic` + `GRAPH_ANTHROPIC_MODEL=claude-haiku-5-5`, no new LLM plumbing). The prompt asks for 1-5 short, lowercase keywords: commodities, sectors, geographic chokepoints, or company names that a prediction-market title might use — not a fixed taxonomy. Returns `[]` immediately without a model call when `summary` is empty/`None`.
- `tag_relationship(session, relationship: EntityRelationship) -> None` (async): calls `generate_tags` through `asyncio.to_thread` (the model call is synchronous, matching F4/F8), sets `relationship.tags`, flushes. Commit is left to the caller, matching the "Saving" convention.
- `tag_missing(session, limit=50) -> int`: selects `entity_relationships` rows where `tags IS NULL`, tags each one, returns the count tagged.
- **Not an API endpoint and not wired into F5/`build_links`.** A one-shot script, run on demand (after a link run, or whenever there's new untagged data) — not a background/long-running process: `tag_all(database_url, batch_size=50) -> int` connects once, calls `tag_missing` repeatedly until a batch comes back smaller than `batch_size` (i.e. nothing left to tag), committing after each batch so a later failure doesn't lose earlier progress, then disposes the engine and returns the total tagged.
- Entry point: `uv run python -m company_graph.tags`, run independently of the FastAPI app and of F11's `__main__.py` dev tools, same as an ingestion module's `__main__.py`. Runs once and exits; no interval, no signal handling, no loop.

**Non-Goals:**
- Comparing tags to Kalshi market titles/categories to find corresponding markets — a separate, later feature.
- Fetching the article at `evidence_url` — `generate_tags` only reads the stored `summary` text; the URL is not scraped.
- A fixed/controlled tag vocabulary — tags are free-form short strings from the model, not validated against a list.
- An API endpoint or a background/polling process — this is a script, run by hand (or later wired into a scheduler/CI step) when there's new data to tag, not something that stays running.

**Done when:** a fixture relationship with the NVIDIA/SK hynix summary above produces tags including something like `memory` or `semiconductors` (model call faked in tests with a canned reply), a relationship with no `summary` produces `[]` and makes no model call, `tag_missing()` only tags rows where `tags IS NULL` (rerunning it twice tags nothing the second time), and `python -m company_graph.tags` run against a database with untagged rows tags all of them in one invocation and exits.
