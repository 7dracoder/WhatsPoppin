# Backend: FastAPI App Structure

**Status:** Draft — scaffolding only, not a complete endpoint list

## Problem / Why

`backend/ingestion/alpaca` (stock prices) and `backend/ingestion/twitter_lookup`
(tweets by account or keyword) both exist and work (verified live against
real credentials), but nothing calls them except manual test scripts.
Nothing in this repo instantiates a FastAPI app at all yet. This spec sets
up the general app structure — where endpoints live, how they reach the
ingestion modules, how credentials get loaded — so endpoints like "get
NVIDIA's price" or "get Trump's recent tweets" have somewhere to go. It
intentionally does not try to be a complete API; it's the skeleton to
build onto.

## Goals

- A runnable FastAPI app (`uvicorn backend.main:app`) with a router per
  domain, starting with stocks and Twitter.
- One working example endpoint per existing ingestion module — a stock
  price lookup (e.g. NVIDIA via [[ingestion/alpaca]]) and a tweet lookup
  (e.g. an account like Trump, or a keyword, via
  [[ingestion/twitter-lookup]]) — proving the structure actually reaches
  real data, not just a stub that returns nothing.
- A place to add more routers later (market history, alerts, etc.)
  without restructuring what's already there.

## Non-Goals

- A complete set of endpoints for every ingestion module — only stocks
  and Twitter get a real endpoint here; everything else is a documented
  gap (see Open Questions), not an oversight.
- Auth, rate limiting, or deployment config — MVP scope, none of
  this is needed yet.
- Rewriting the ingestion gateways as async — they're sync `httpx.Client`
  calls today; FastAPI runs sync `def` endpoints in a threadpool
  automatically, so this works without touching
  [[ingestion/alpaca]]/[[ingestion/twitter-lookup]]'s existing code.
- CORS, frontend wiring, or anything about how `frontend/` actually calls
  this — flagged as an Open Question, not solved here.

## User Stories / Example Interactions

- As a developer, I want to run one command and hit an endpoint that
  returns NVIDIA's real price data, to confirm the backend is wired up
  before building a chart against it.
- As a developer, I want to hit an endpoint that returns a looked-up
  account's (e.g. Trump's) recent tweets, same reason.
- As a developer adding a new feature later (market history, alerts), I
  want an obvious place to add a new router without re-reading this whole
  file.

## Functional Requirements

1. `backend/main.py` instantiates `FastAPI()` and includes each domain
   router. Runnable via `uv run uvicorn backend.main:app --reload`.
2. `backend/api/deps.py` builds each ingestion module's gateway once from
   `.env` (`ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`, `X_BEARER_TOKEN`)
   as a FastAPI dependency, so a gateway isn't rebuilt from env vars on
   every request.
3. `backend/api/stocks.py`: a router with one endpoint,
   `GET /stocks/{ticker}/prices?tier=...`, calling
   [[ingestion/alpaca]]'s `fetch_price_series()` and returning the
   `PricePoint` list as JSON.
4. `backend/api/twitter.py`: a router with two endpoints,
   `GET /twitter/account/{handle}` and `GET /twitter/search?query=...`,
   calling [[ingestion/twitter-lookup]]'s `lookup_account()` /
   `lookup_keyword()` and returning the `ContentItem` list as JSON.
5. Map each ingestion module's own error type (`AlpacaApiError`,
   `TwitterApiError`) to an HTTP response (e.g. 502 for a retryable
   upstream failure, 400 for a bad ticker/handle) rather than letting an
   unhandled exception 500 out.

## Design / Approach

```
backend/
  main.py           # FastAPI() instance, includes routers
  api/
    deps.py          # shared dependencies: get_alpaca_gateway(), get_twitter_gateway()
    stocks.py        # /stocks router
    twitter.py       # /twitter router
  ingestion/
    alpaca/          # already built
    twitter_lookup/  # already built
```

Each router is thin — it parses the request, calls the ingestion
module's existing public function (`fetch_price_series`,
`lookup_account`, `lookup_keyword`), and serializes the result. No
business logic lives in the router itself; that's already in
`ingestion/*/service.py`.

## Interfaces / Data Model

Two example endpoints, to prove the structure works end to end:

```
GET /stocks/{ticker}/prices?tier=recent|daily|weekly|monthly|all_time
-> [{"source": "alpaca", "market_id": "NVDA", "price_or_odds": 229.47,
     "volume": 188, "timestamp": "2026-10-09T20:10:00Z"}, ...]

GET /twitter/account/{handle}?start=...&end=...
GET /twitter/search?query=...&start=...&end=...
-> [{"source": "twitter", "author": "realDonaldTrump", "text": "...",
     "entities": [], "engagement": {...}, "timestamp": "..."}, ...]
```

Response models are the existing `PricePoint` / `ContentItem` Pydantic
models from each ingestion module — no new schema invented here.

## Dependencies

- [[ingestion/alpaca]], [[ingestion/twitter-lookup]] — already built,
  this spec just exposes them.
- `fastapi` + `uvicorn` — not yet in `pyproject.toml`; need adding.
- `python-dotenv` — already a dependency, used to load `.env` once at
  app startup (same as every other entry point in this repo).

## Open Questions

- ~~CORS~~ Resolved: `backend/main.py` allows the Vite dev server
  (`http://localhost:5173` and `http://127.0.0.1:5173`) for GET requests,
  in `FRONTEND_ORIGINS`. Add a deployed frontend's origin there when one exists.
- Where do market-history (Kalshi/Polymarket, per [[ingestion/alpaca]]'s
  deferred Resolved note) and alert endpoints eventually live — new
  routers under `backend/api/`, following the same pattern, presumably,
  but not built here. Also fine to leave for later; this spec is a
  skeleton, not a complete endpoint list.

## Acceptance Criteria

- `uv run uvicorn backend.main:app --reload`, then `GET
  /stocks/NVDA/prices?tier=daily` returns real NVDA price data (same
  shape the manual test script already confirmed working).
- `GET /twitter/account/realDonaldTrump` returns real tweet content
  items (same shape the manual test script already confirmed working).
