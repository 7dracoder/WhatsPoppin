# company_graph

Backend for the Company Graph page: for a searched company, the companies it does business
with (links, from SEC filings), the linked companies a recent event may affect
(highlights, from news and prediction-market alerts) and its directors (board members, from
SEC insider ownership filings).

Spec: `new_specs/ingestion/company-graph-tasks.md`.

## What is built

| Module | Feature | What it does |
| --- | --- | --- |
| `companies.py` | F1 | SEC ticker list, `resolve`, `search_companies`, `ensure_entity`, `aliases_for` |
| `sec.py` | F2 | Rate-limited, cached SEC client: filings, filing text, full-text search |
| `trim.py` | F3 | Cuts a filing down to the passages likely to state a relationship |
| `config.py`, `db.py`, `schemas.py`, `fixtures/` | F0 | Settings, tables, API shape, fake-mode data |
| `llm.py`, `extract.py` | F4 | `complete` over the shared `complete_structured`; `extract` reads one passage for a relationship; `save_relationship` validates and upserts into `entity_relationships` |
| `news_events.py` | F6 | One NewsAPI request per graph, event typing (Jev, or Claude with `GRAPH_LLM_PROVIDER=anthropic`), saved to `graph_events`. Entry point: `refresh_news_events` |
| `links.py` | F5 | `build_links(symbol)`: own 10-K, reverse full-text search, recent 8-Ks, sector fallback, into `entity_relationships`; `read_links` and `get_link_run` for the API |
| `market_events.py` | F7 | Recent `alerts` (read-only) matched to the companies their markets name, saved to `graph_events` and `market_entities`. Entry point: `fetch_market_events` |
| `highlights.py` | F8 | `build_highlights(symbol, session=...)`: refreshes news and market events for the graph, asks the model which linked companies each recent event involves, saves `graph_highlights` with a direction and the Alpaca price change |
| `api.py` | F9 | `GET /graph/{ticker}`, `GET /graph/{ticker}/board` and `GET /companies/search`; starts link runs, highlight runs and board runs in the background |
| `boards.py` | Board | `build_board(symbol)`: the company's recent Forms 3 and 4, parsed for reporting owners marked as directors, into `graph_board_seats`; `read_board` and `get_board_run` for the API. No model call |

Not built yet: demo commands (F11).

### Conventions the next features rely on

- **Link direction (F4):** the filer is `entity_symbol`, and the type is the other company's role for it ("supplier" means it supplies the filer). Flip with `extract.reverse_type`.
- **Evidence URLs (F4):** `save_relationship` takes `fetched_urls`, the URLs downloaded in the current run, and rejects any other evidence URL. This applies to `sector` links too, so the fallback must pass a URL it fetched (such as the SEC submissions JSON). A `sector` save never overwrites a `filing` row.
- **SEC client (F2):** each `SecClient` has its own rate limiter, so one link run must share one client.
- **News cache (F6):** results per company, the daily request budget and Jev labels live in `.cache/company_graph/news_cache.json`. Deleting it only costs a few repeated requests. The budget lock covers one process.
- **Market names (F7):** a market names a company only through its full name or a cashtag (`$TSLA`); bare tickers and common words never match. Short names for Palantir, Uber, Disney, Ford and Delta come from the built-in seed aliases in `backend/entities/data/sp500_top50.json`.
- **Model provider (F4, F8):** `llm.complete` uses the shared OpenRouter client by default, or the Anthropic SDK directly with `GRAPH_LLM_PROVIDER=anthropic` (structured outputs, low effort, server-side fallbacks on models that support them). Every failure is an `LlmError` either way. The team's OpenRouter default model is `anthropic/claude-haiku-5.5`; `GRAPH_LLM_MODEL` overrides it.
- **X posts (F12, `social_events.py`):** one X recent search per batch of companies (one page, relevancy order, no retweets or replies), cached in `.cache/company_graph/social_cache.json` with its own daily budget. Stock lists and posts under `GRAPH_SOCIAL_MIN_LIKES` likes are dropped. Jev is asked once per (post, company it names) whether the post states an event about that company as fact; anything else, or a probability under 0.9, is ignored. Kept posts are saved as `graph_events` with source `social`, and the highlight builder treats them like news. Without `X_BEARER_TOKEN` the step is left out.
- **Pair news (F13, `pair_news.py`):** `GET /graph/{ticker}/news/{other}` searches NewsAPI (`"A" AND "B"`, 30 days) and X (both names, 7 days) once per pair, keeps what Jev says is about the two together (title plus the article's opening text), and caches it per pair in `.cache/company_graph/pair_cache.json`. It answers only for companies linked in the stored graph. The page shows it on a linked company's panel.
- **Sync calls:** `llm.complete`, `extract.extract` and Jev are synchronous. Call them through `asyncio.to_thread`.
- **Saving:** every save function flushes and leaves the commit to the caller. The exception is `build_links`, which commits as it goes so pollers see progress: give it its own session.
- **Pre-building for hops:** `uv run python -m company_graph.prebuild [TICKERS] [--neighbors NVDA] [--force]` runs link builds sequentially for a list (default: the seed S&P companies in `backend/entities/data/sp500_top50.json`), so the workspace hop slider has a second ring without on-demand builds during a demo. Each run reads the two newest annual reports (`ANNUAL_REPORTS`) plus recent 8-Ks.
- **Reading links (F5):** call `links.read_links(session, symbol, cfg.graph_max_linked)`, never a plain `entity_symbol = symbol` query. Reverse-lookup links are stored from the other company's side and `read_links` flips them.
- **Run status (F5):** a timeout ends `done` with what was saved. A run that saved no filing link and hit any failure (SEC, search or the model) ends `error`, even if industry peers were saved, so an outage is not cached for the TTL. A `running` row older than 10 minutes counts as crashed.
- **Which filings are read (F5):** the reverse search keeps one filing per other company and reads the 10 largest companies first. Size is the company's position in SEC's `company_tickers.json`, which SEC orders largest first (`CompanyDirectory.size_rank`). SEC's own order favours small companies that depend on the searched one.
- **Industry on the graph (F9):** `GET /graph/{ticker}` gives the searched company and each node an `industry`, SEC's SIC description from `graph_company_profiles` (`api.read_industries`). A link run saves the description only for the company it ran for; companies found through the reverse search have the code alone and borrow the description from another company in the same response with that code. Otherwise `industry` is null. Search results and boards do not carry it.
- **Industry peers (F5):** every run adds the 3 largest companies in SEC's list for the searched company's industry (SIC) code as `sector_peer` links (up to `GRAPH_MAX_LINKED` when there is no filing link), citing that list (`SecClient.ciks_by_sic`). Companies already linked from filings are not repeated.
- **What counts as a relationship (F4):** the prompt excludes landlords and leases, lenders, insurers, auditors, law firms, shareholders, lawsuit opponents, ended relationships and anything hypothetical.
- **SEC retries (F2):** 429, 500, 502, 503 and 504 are retried with backoff.
- **Processed filings (F5):** own filings are keyed by accession number; reverse reads by `accession#SYMBOL`, so one big 10-K can serve several companies' graphs.
- **Event typing provider (F6):** with `GRAPH_LLM_PROVIDER=anthropic`, `classify_event` asks Claude through `llm.complete` (the same five event types plus `none`, as `EventLabel`) and needs no `OPENROUTER` key; otherwise it uses Jev as before. Labels are cached per URL in the news cache either way.
- **Which companies an event can reach (F8):** one hop along the searched company S's links. An event about S is offered to the model with all of S's linked companies; an event about a linked company L is offered with S only (target S, so it shows wherever S is a node, such as L's graph, not on S's own page). Only companies the model names from that list get a highlight.
- **Highlight direction (F8):** from the target's role on the link, never from the model: supplier, customer, partner -> `may_benefit`; competitor -> `may_face_pressure`; `sector_peer` -> `may_face_pressure` only when the model marks the event a direct competitive gain over that peer (`competitive_gain`), otherwise no highlight. Industry peers have no stated relationship, so only a clear win gives a defensible direction.
- **Highlight wording (F8):** one factual sentence per highlight; reasons that read like a price prediction or trade suggestion are dropped (`highlights.is_factual`). `price_change_pct` is the past change from the last bar at or before the event to the latest bar, null when the Alpaca keys are missing, the call fails, or the company has no US ticker.
- **Remembered evaluations (F8):** which (searched company, event) pairs the model has judged, and with which candidates, live in `.cache/company_graph/highlight_evals.json` (`EvalStore`), pruned after `GRAPH_EVENT_WINDOW_DAYS`. An event is asked about again only when the searched company has gained a linked company since, or the last call failed. Deleting the file only costs repeated model calls: a highlight is never stored twice for the same (event, target). A JSON file rather than a table because `db.create_tables` lists its tables explicitly.
- **Highlight runs (F8, F9):** `build_highlights` commits as it goes (each refresh step, then the highlights), so give it its own session. News and market refreshes are optional: no `NEWSAPI_KEY`, no `alerts` table or any failure is logged and skipped. At most `MAX_EVENTS_PER_RUN` (40) events, newest first, go to the model per run, 4 at a time: one model call per new event.
- **API status with highlights (F9):** once links are done, the first request (and the first after `GRAPH_NEWS_TTL_HOURS`, or `ERROR_RETRY_S` after a failed run) starts `build_highlights` as a background task with its own session and answers `running` with the links; at most one highlight task per company in a process (`api._HIGHLIGHT_RUNS`; last end times in `api._HIGHLIGHTS_DONE`, in memory). A failed highlight run is logged and the graph is `done` with the highlights already stored.

- **Board source (Board):** Forms 3 and 4 (and their amendments), which every director and officer files, not the proxy statement: the filing is structured XML, so no model reads it. `SecClient.fetch_raw` returns the XML; `fetch_text` would strip the tags. SEC lists the primary document as the rendered view (`xslF345X05/form4.xml`); the XML is the same file name without that folder (`boards.raw_xml_document`). A filing whose issuer is another company (the searched company reporting its own stake in someone else) is skipped.
- **Person identity (Board):** a person is their own SEC CIK (`person_cik`, shown as `cik-0001234567`), which is the same in every company's filings. Two seats with one `person_cik` are one person on two boards; names are never compared. Board seats are not in `entities` or `entity_relationships`: `read_links` and the highlight builder assume every relationship row is company to company.
- **Who is on the board (Board):** per person the newest filing read decides, and only people it marks `isDirector` are kept (`boards.pick_directors`). The role is the officer title when there is one, otherwise "Director". A run reads the newest `GRAPH_BOARD_MAX_FILINGS` filings of the last 15 months (`boards.LOOKBACK_DAYS`). A stored seat is deleted only when a newer filing says the person is not a director or its filing is older than the lookback, so a director missed by one run's cap is kept. A director who left stays until then.
- **Names (Board):** filed names are "LAST FIRST MIDDLE"; `boards.display_name` shows "First Middle Last" and `raw_name` keeps the filed spelling. The first word is taken as the surname, so a two-word surname comes out wrong.
- **Board runs (Board, F9):** `build_board` commits (the `running` row, then the seats and the final status), so give it its own session, and it shares one `SecClient` per run like a link run. Status lives in `graph_board_runs`, never `graph_link_runs`, and reuses `links.is_fresh` / `is_in_progress`: fresh for `GRAPH_BOARD_TTL_DAYS`, a `running` row older than 10 minutes counts as crashed. No Form 3 / 4 filings (many foreign issuers) ends `done` with no members. A timeout ends `done` with what was read. A run that read no filing and hit any failure ends `error`, reported for `ERROR_RETRY_S` and then retried. At most one board task per company in a process (`api._BOARD_RUNS`). The board endpoint never starts a link or highlight run.

## Setup

Add to `.env` (see `.env.example` for the shared keys and the company graph block):

```
DATABASE_URL=postgresql://...        # the shared database
SEC_CONTACT_EMAIL=you@example.com    # SEC rejects requests without a contact
OPENROUTER=...                       # model calls through backend/llm and Jev (F6)
GRAPH_LLM_PROVIDER=anthropic         # optional: F4/F8 call Claude directly instead of via OpenRouter
ANTHROPIC_API_KEY=sk-ant-...         # needed when GRAPH_LLM_PROVIDER=anthropic
# GRAPH_ANTHROPIC_MODEL=claude-haiku-5-5   # optional: cheaper than the default claude-opus-5-5
NEWSAPI_KEY=...                      # news events (F6); without it highlights use market alerts only
ALPACA_API_KEY_ID=...                # optional: price_change_pct on highlights (F8)
ALPACA_API_SECRET_KEY=...
# Optional, defaults in config.py:
# GRAPH_LINK_TTL_DAYS=7  GRAPH_EVENT_WINDOW_DAYS=7  GRAPH_MAX_LINKED=20
# GRAPH_NEWS_TTL_HOURS=6  GRAPH_NEWS_DAILY_BUDGET=40  GRAPH_FAKE=0  GRAPH_LLM_MODEL=...
# GRAPH_BOARD_TTL_DAYS=7  GRAPH_BOARD_MAX_FILINGS=40
```

To check the extractor against the real model (it costs a few model calls):

```
GRAPH_LIVE_EVAL=1 uv run pytest tests/company_graph/test_extract.py -k live
```

To check a full link run against SEC, the model and the shared database (writes only `entities`, `entity_relationships` and the `graph_` tables):

```
GRAPH_LIVE_EVAL=1 uv run pytest tests/company_graph/test_links.py -k live
```

## Tables

`company_graph.db` imports its model modules, so the shared `connect(DATABASE_URL)` from
`backend.ingestion.common.db` creates them along with the market tables. To create only
this feature's tables on an existing engine:

```python
from company_graph.db import create_tables, make_engine

async with make_engine(url).begin() as conn:
    await conn.run_sync(create_tables)
```

| Table | Shared? | Holds |
| --- | --- | --- |
| `entities` | yes (db-design.md) | Companies, people, industries |
| `entity_relationships` | yes, plus `summary` and `evidence_url` | Graph links |
| `market_entities` | yes | Which market names which company |
| `graph_company_profiles` | no | CIK, SIC code, listing venue |
| `graph_link_runs` | no | Link-building status per company |
| `graph_processed_filings` | no | Filings already read |
| `graph_events` | no | News and market events, last 7 days |
| `graph_highlights` | no | Linked companies an event may affect |
| `graph_board_runs` | no | Board-building status per company |
| `graph_board_seats` | no | Directors per company, one row per (company, person CIK) |

No table has a foreign key to `markets` or `alerts`: `python -m backend.ingestion.reset_db`
drops those with CASCADE. Never run `reset_db` for this feature, and never write to
`alerts`, `markets` or the `market_*` tables.

## Fake mode

`GRAPH_FAKE=1` serves `fixtures/{TICKER}.json` (TSLA, AAPL, NVDA) through
`schemas.load_fixture`. The frontend has identical copies in
`frontend/src/features/company-graph/fixtures/` for `VITE_GRAPH_FAKE=1`; a test fails if the
two drift apart.

`GET /graph/{ticker}/board` serves `board_fixtures/{TICKER}.json` through
`schemas.load_board_fixture` (TSLA, AAPL, NVDA and some of their linked companies; any other
ticker is an empty `done` board). They are in their own folder because every file in `fixtures/`
must have a frontend copy. The people are invented, with ids outside the range SEC has issued
(`cik-99900000xx`): never put a real person in a made-up board. Some sit on two fixture boards
(TSLA and NVDA share one), so interlocks can be shown.

## Tests

```
uv run pytest tests/company_graph
```

No test needs a network connection or a running Postgres: table tests use SQLite and also
compile the Postgres DDL. `tests/company_graph/conftest.py` clears the model settings and the
`NEWSAPI_KEY`, `ALPACA_*`, `OPENROUTER` and `ANTHROPIC_API_KEY` keys for every test, and makes
any real Anthropic, OpenRouter or Jev call fail.
