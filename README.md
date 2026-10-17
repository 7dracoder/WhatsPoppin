# What'sPoppin

**Prediction markets, news and social chatter, mapped to the companies they move.**

What'sPoppin watches Kalshi and Polymarket in real time, works out which public
companies each market actually matters to, and lines the market's odds up
against the stock price, with the news and X posts that explain the moves.
When a market makes an unusual move, it flags it and traces it through a
supply-chain graph built from SEC filings to every company that may feel it.

![Company graph for NVDA](docs/screenshots/02-company-graph.png)

---

## The problem

Prediction markets now price real-world events minute by minute: Fed decisions,
export controls, tariffs, earnings, elections. That is an early, liquid read on
risks that hit stocks. But using it today means:

- **Thousands of markets, no map to companies.** Nothing tells you that a
  "chip export controls" market matters to NVIDIA, its supplier TSMC and its
  competitor AMD.
- **Moves are noticed late.** Nobody watches every market by hand. Most
  people find out from social media after the move is priced in.
- **"Why did it move?" is manual work.** Connecting an odds jump to a stock
  move and to the headline behind it means juggling five tabs.

## What What'sPoppin does

| | |
|---|---|
| **Company graph** | Search any listed company. Its suppliers, customers, partners, competitors and board interlocks are extracted from SEC 10-K/10-Q and insider filings, each link with the filing it came from. |
| **Markets that matter** | Every open Kalshi and Polymarket market is tagged with the companies, sectors and themes it affects. A company gets its own markets first, then its linked companies' markets, then markets matching its themes. Or describe a risk in plain English ("war in the middle east oil supply") and semantic search finds the matching markets. |
| **Price vs. odds** | The stock and the market's odds on one timeline (1D to all-time), market-adjusted against SPY, with a correlation and lead/lag readout. |
| **Evidence on the chart** | Pull in news articles and X posts. Each one is classified for sentiment and pinned to the moment it was published, so you can see what came before the move. |
| **Ask the evidence** | Run questions over the evidence you collected, e.g. *"what % of these are about supply-chain risk?"* |
| **Live alerts** | A detector scores every market every second against its own history (price z-score, volume bursts, whale orders, order-flow imbalance) and raises an alert only on real moves. Each alert is matched to the companies it may affect, and their nodes pulse on the graph. |
| **Projects** | Save an analysis (graph, markets, evidence, range) and reopen it later. |
| **Poppy, the voice assistant** | Click the speaker button next to the theme dots and talk to the app. Poppy sees what's on screen and can drive it: open a company's graph, find and pick markets, open the chart, change the range, theme or mode, open alerts, and answer questions about what it shows. Hands-free mode keeps the conversation going, and narration reads out what just loaded. It stays on as you move between pages. |

### Walkthrough

| 1. Search a company | 2. Find the markets that matter |
|---|---|
| ![Search](docs/screenshots/01-search.png) | ![Market search](docs/screenshots/03-market-search.png) |

| 3. Stock vs. market odds, with evidence |
|---|
| ![Price vs odds](docs/screenshots/04-price-vs-odds.png) |

| 4. "Show me Nvidia, please" (spoken) | 5. "What does the chart show?" |
|---|---|
| ![Voice assistant builds the graph](docs/screenshots/06-voice-graph.png) | ![Voice assistant explains the chart](docs/screenshots/07-voice-chart.png) |

---

## Architecture

```mermaid
flowchart LR
    subgraph Sources
        K[Kalshi<br/>WebSocket + REST]
        P[Polymarket<br/>Gamma + CLOB WS]
        SEC[SEC EDGAR<br/>filings, Forms 3/4]
        N[NewsAPI]
        X[X / Twitter API]
        A[Alpaca<br/>stock bars]
    end

    subgraph Workers["Background workers (Python, asyncio)"]
        ING[Ingestion<br/>kalshi / polymarket / polymarket_us]
        BASE[Baseline job<br/>per-market normals]
        DET[Alert detector<br/>4 signals, 1s loop]
        ENR[Enrichment<br/>market → company tags]
        IMP[Alert impact<br/>alert → affected companies]
    end

    subgraph AI["AI layer (Nicrron gateway)"]
        LLM[LLM client<br/>structured extraction]
        JEV[Jev classifier<br/>sentiment / yes-no / multi-label]
        EMB[Embeddings<br/>voyage-finance-2]
    end

    DB[(PostgreSQL)]

    subgraph API["FastAPI backend"]
        R1[/graph · companies/]
        R2[/entities · top-markets/]
        R3[/markets · history/]
        R4[/evidence · twitter · stocks/]
        R5[/classification/]
        R6[/alerts · projects/]
        R7[/assistant/]
        CG[Company graph builder<br/>links · highlights · boards]
    end

    UI[React app<br/>graph · chart · evidence<br/>voice assistant]
    EL[ElevenLabs<br/>speech-to-text · text-to-speech]
    TR[(PRISM run traces)]

    K --> ING
    P --> ING
    ING --> DB
    DB --> BASE --> DB
    DB --> DET --> DB
    DB --> ENR --> DB
    DB --> IMP --> DB
    ENR --> EMB
    ENR --> JEV
    IMP --> JEV
    R3 --> EMB
    SEC --> CG
    N --> CG
    X --> CG
    CG --> LLM
    CG --> DB
    A --> R3
    A --> R4
    N --> R4
    X --> R4
    R5 --> JEV
    DB --> R2
    DB --> R6
    DB --> R1
    UI <--> API
    R7[/assistant/] --> EL
    R7 --> LLM
    R7 --> TR
    R3 --> TR
```

### How data flows

1. **Ingestion** workers stream live quotes and trades for the most-traded open
   markets into `markets`, `market_prices` and `market_trades`. Every venue is
   normalized to the YES side's point of view, keyed by `(source, market_id)`.
2. The **baseline job** pulls days of history per market and stores its normal
   5-minute move, normal dollar volume and 99th-percentile order size.
3. The **alert detector** re-scores every updated market once a second against
   that baseline and writes an `alerts` row (with a Postgres `NOTIFY`) when a
   move is real.
4. **Enrichment** tags each market event with the companies and themes it
   affects (`market_entities`):
   - the classifier picks the categories,
   - embeddings shortlist the closest entities in each,
   - one classifier call confirms them.
5. **Alert impact** listens for new alerts and matches each one to companies
   in three tiers:
   - **Tier 1:** the market names the company directly.
   - **Tier 2:** the company is linked in the graph to a tier-1 company.
   - **Tier 3:** the classifier judges the company relevant.
6. The **company graph** is built on demand when a company is first searched:
   - **Links:** filings are trimmed to relationship passages, then the LLM
     extracts each relationship with its evidence URL.
   - **Highlights:** recent news, X posts and alerts about linked companies are
     classified for impact.
   - **Boards:** directors are read from Forms 3 and 4.
   - Everything is cached in Postgres with a TTL.
7. The **React app** reads all of this over REST. Charts are fetched live from
   the venue (Kalshi candlesticks, Polymarket price history) and from Alpaca,
   then aligned on one clock.

### The alert signals

The detector looks at a 5-minute window and compares it against each market's
own baseline. It never uses one global threshold.

| Signal | Fires when |
|---|---|
| **Price move** | \|z\| ≥ 3, where z = 5-min midpoint move ÷ typical 5-min move. Good quotes only (two-sided book, spread ≤ 10 pts). |
| **Volume burst** | Dollars traded ≥ 5× normal and ≥ $300. |
| **Whale** | A single order larger than the market's 99th-percentile order (≥ $500). |
| **Imbalance** | ≥ 60% of taker dollars on one side. Only counts alongside a burst. |

Alerts are grouped by event: the strongest strike is the alert and the others
are context. A 10-minute cooldown applies unless a new move scores 1.5× the
last one. Markets that are near close, pinned near 0/1, or have just reconnected
are excluded.

### AI layer

All model calls go through the **[Nicrron](https://nicrron.ai) gateway** with one key
(OpenAI-compatible, `nicrron/auto` routing by default), so models can be swapped with one
setting and no code changes. Calls retry on rate limits, using `Retry-After`.

| Piece | Nicrron model | What it does |
|---|---|---|
| `backend/llm/client.py` | `nicrron/auto` | Structured extraction (graph relationships, highlights). JSON is validated against a Pydantic schema, and invalid output is rejected rather than guessed. |
| `backend/classification` (Jev contract) | `nicrron/auto` | A typed question contract with three question types: `noul` (yes/no probability), `choice` (one label with per-label probabilities) and `score` (ordered rubric). On top of these sit the sentiment, boolean, choice, multi-select and score modes. A chat model answers, and the reply is normalized into the calibrated Jev answer shape. |
| `backend/llm/embeddings.py` | `voyage/voyage-finance-2` | Finance-tuned embeddings (1024-d) used for retrieval in front of the classifier. |

**Retrieve, then judge.** Embeddings choose *what to ask*, and the classifier decides *what is true*:

- **Semantic market search** (`GET /markets/search`): a plain-English risk is
  embedded and ranked against every open market event (cosine similarity, one
  market per event, untraded markets dropped). Event vectors are stored in
  `market_embeddings` and only re-embedded when the market text changes.
- **Enrichment shortlist:** instead of asking the classifier about every entity
  in every chosen category, each event's embedding retrieves the 12 closest
  entities per category, and the classifier checks them all in **one** call.
  The classifier still has to say yes to each candidate at the threshold.

Measured on the same 20 live markets (`scripts/compare_enrichment.py`):

| | v1: full category walk | v2: embedding shortlist |
|---|---|---|
| Classifier calls | 77 | **40** (2 per event, flat) |
| Same tags as v1 | — | 15/20 events identical, 88% of v1's tags found |
| Worker throughput | ~8.7 s/event | **~2.8 s/event** |

Most of the tags v2 misses are second-order, like `Pete Hegseth` on a ceasefire
market or `Oil and Gas` on an airspace closure. Set `ENRICH_SHORTLIST=0` to go
back to the full walk.

### Voice assistant (Poppy)

One spoken turn runs through three backend routes. The ElevenLabs key never reaches the browser.

```mermaid
sequenceDiagram
    participant U as You
    participant B as Browser (AssistantProvider)
    participant A as FastAPI /assistant
    participant E as ElevenLabs
    participant N as Nicrron (nicrron/auto)
    U->>B: speak (mic, silence detection ends the turn)
    B->>A: POST /assistant/transcribe (audio)
    A->>E: Scribe speech-to-text
    E-->>B: "show me nvidia"
    B->>A: POST /assistant/ask (words + snapshot of the screen)
    A->>N: system prompt + screen context + history
    N-->>B: {reply, actions: [search_company NVDA]}
    B->>B: run actions (dispatch to the workspace / panels)
    B->>A: POST /assistant/speak (reply)
    A->>E: text-to-speech (Flash v2.5)
    E-->>U: audio reply, then (hands-free) listen again
```

- **What Poppy sees:** a JSON snapshot of the screen, built fresh for every question:
  - the stage and the company,
  - linked companies and their relationship,
  - recent events and board members,
  - the numbered market list, selected markets and live alerts,
  - the chart's start/end prices and odds and its correlation readout,
  - the pinned evidence with sentiment,
  - the theme.
- **What Poppy can do:** a fixed list of actions, checked against a schema on the server: `search_company`, `search_markets`, `select_market`, `generate_chart`, `back_to_graph`, `set_range`, `set_theme`, `set_mode`, `go_home`, `open_alerts`, `stop_listening`. Panels own their actions through a small command bus (`features/assistant/assistantBus.ts`).
- **Modes:**
  - **Hands-free:** listens again after every reply.
  - **Narration:** speaks when a graph finishes or a chart opens.
  - **Alt+V:** talk/stop from anywhere.
  - **Typing:** you can type instead of speaking.
- **Guardrail:** Poppy gives information, not advice. It won't recommend trades, predict prices or promise returns, and it only names companies, markets and numbers that are on screen.

### Run tracing (PRISM)

Every assistant turn and market search is recorded as a **run** with timed **steps**, by `backend/observability/prism.py`:

| Run | Steps |
|---|---|
| `assistant.turn` | `context.snapshot` (retrieval) → `llm.complete_structured` (model) → one `action.*` per UI action |
| `markets.search` | `embeddings.search` (retrieval) → `venue.activity_filter` (tool) |

Each step records its input, output, duration and status. A failing step marks the run failed, with the error type and message, which is what failure clusters group on. Runs are appended to `.cache/prism/runs.jsonl`. With `PRISM_API_KEY` and `PRISM_PROJECT_ID` set, each run is also sent to PRISM on a background thread, so tracing never slows a request down: the run as a trace (`/api/traces`) and its steps as that trace's spans (`/api/spans/ingest`).

**Grounding rules:** every graph link and highlight must carry a source URL from
a real fetch in that run. Nothing is saved from model memory, and no wording
predicts a price or suggests a trade.

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2 (async) + asyncpg, httpx, Pydantic 2, NumPy, `uv` |
| Database | PostgreSQL (tables created from the models; no migrations) |
| Frontend | React 18, TypeScript, Vite, Tailwind, Radix Themes, Motion, react-force-graph-2d, Recharts, Web Audio + MediaRecorder |
| Voice | ElevenLabs Scribe (speech-to-text) and Flash v2.5 (text-to-speech), proxied by the backend |
| AI | Nicrron gateway: `nicrron/auto` for reasoning, `voyage/voyage-finance-2` for embeddings; optional TypeSafe Jev, Anthropic |
| Data | Kalshi, Polymarket (global + US), SEC EDGAR, NewsAPI, X API, Alpaca |
| Tests | pytest (650+ tests), Vitest (120+ tests) |

## Repository layout

```
backend/
  main.py              FastAPI app: CORS, error handling, routers
  api/                 REST routes (graph, entities, markets, evidence, classification, alerts, projects, assistant…)
  ingestion/           kalshi/, polymarket/, polymarket_us/, alpaca/, news_api/, twitter_lookup/, common/
  enrichment/          market → company tagging worker + search queries
  alert_impact/        alert → affected companies worker (tiers 1–3)
  classification/      Jev question contract, modes, relevance filter, sentiment cache
  llm/                 Nicrron client: structured output (client.py) + embeddings (embeddings.py)
  entities/            entity map (companies, sectors, themes) + matcher
  evidence/            news fetch + cache
  observability/       run tracing (PRISM): runs with timed steps
  models/              SQLAlchemy models, one per table
alert_detector/        live signal detector (signals.py = pure maths)
baselines/             per-market "normal" computed from history
company_graph/         SEC client, filing trimmer, relationship extractor, links, highlights, boards
frontend/src/
  features/workspace/  graph, markets panel, market view (chart, evidence), alerts, projects
  features/assistant/  Poppy: provider (voice loop), recorder (silence detection), dock, command bus
  lib/                 API client, chart maths (alignment, correlation, lead/lag)
new_specs/             design specs per feature
mock_db_design/        database design
scripts/               init_db.py, benchmarks, tunnels
tests/                 backend tests mirroring the package layout
```

---

## Run it locally

**Requirements:** Python 3.11+ with [uv](https://docs.astral.sh/uv/), Node 20+, PostgreSQL.

```bash
# 1. Configure
cp .env.example .env                    # set DATABASE_URL and your keys (see below)
cp frontend/.env.example frontend/.env

# 2. Install
uv sync
(cd frontend && npm install)

# 3. Create the database tables (once; safe to re-run)
uv run python scripts/init_db.py

# 4. Start the API (http://localhost:8000) and the web app (http://localhost:5173)
uv run uvicorn backend.main:app --reload
cd frontend && npm run dev
```

Open **http://localhost:5173**. The API's CORS only allows the app on port 5173.

> **No SEC setup yet?** Set `GRAPH_FAKE=1` in `.env` to use built-in graphs for
> AAPL, NVDA and TSLA with no outside calls.

### Live data workers

Run each in its own terminal:

```bash
uv run python -m backend.ingestion.polymarket   # live Polymarket markets + prices (no key)
uv run python -m backend.ingestion.kalshi       # live Kalshi markets + prices
uv run python -m baselines                      # per-market baselines (needed by the detector)
uv run python -m backend.enrichment             # tag markets with the companies they affect
uv run python -m alert_detector                 # unusual-move alerts
uv run python -m backend.alert_impact           # alert → affected companies
```

## Configuration and API keys

| Variable | Used for | Get it at | Without it |
|---|---|---|---|
| `DATABASE_URL` | Everything stored: markets, alerts, graph, projects | any Postgres | Most features unavailable |
| `NICRRON_API_KEY` (+ `NICRRON_MODEL`, default `nicrron/auto`) | All LLM + classifier calls: market tagging, sentiment, evidence queries, graph extraction | [nicrron.ai](https://nicrron.ai) | No recommended markets; rough sentiment fallback |
| `SEC_CONTACT_EMAIL` | SEC EDGAR access (an email, not a key) | — | Use `GRAPH_FAKE=1` |
| `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` | Stock prices | [alpaca.markets](https://app.alpaca.markets) (free) | Placeholder stock line |
| `NEWSAPI_KEY` | News evidence + graph highlights | [newsapi.org](https://newsapi.org) (free) | Placeholder news |
| `X_BEARER_TOKEN` | X posts as evidence + highlights | [developer.x.com](https://developer.x.com) | Placeholder posts |
| `KALSHI_API_KEY_ID` + `KALSHI_PRIVATE_KEY_PATH` | Kalshi live stream | kalshi.com → API keys | Kalshi charts still work (public) |
| `POLYMARKET_US_KEY_ID` / `_SECRET_KEY` | Polymarket US stream | polymarket.us/developer | Global Polymarket needs no key |
| `TYPESAFE_API_KEY` | Optional: native Jev model for classification | typesafe.ai | Classifier runs on Nicrron |
| `NICRRON_EMBED_MODEL` | Optional: embedding model | — | `voyage/voyage-finance-2` |
| `ELEVENLABS_API_KEY` | Voice assistant: speech-to-text + text-to-speech | [elevenlabs.io](https://elevenlabs.io) | Voice off; the typed assistant still works |
| `ELEVENLABS_VOICE_ID` | Optional: Poppy's voice | ElevenLabs voice library | "Matilda" |
| `PRISM_API_KEY` + `PRISM_PROJECT_ID` | Optional: export run traces | [PRISM](https://prism.blockconvey.com) (API keys; Settings → Project) | Runs are still logged locally |

All tuning knobs (alert thresholds, cache TTLs, daily API budgets) are
documented in [`.env.example`](.env.example).

## API overview

| Route | What it returns |
|---|---|
| `GET /companies/search?q=` | Company autocomplete (SEC list) |
| `GET /graph/{ticker}` | Company graph: nodes, links with evidence, highlights (polled while building) |
| `GET /graph/{ticker}/board` · `/graph/{ticker}/news/{other}` | Directors · news about two linked companies together |
| `GET /entities/{company}/top-markets` | Markets that matter to a company, ranked by tier |
| `GET /markets/search?q=` | Semantic search: open markets about a plain-English risk or event |
| `GET /markets/{id}/history?stock=&range=` | Market odds + stock bars on the same interval |
| `GET /evidence/news` · `GET /twitter/search` | Candidate evidence items |
| `POST /classification/sentiment` · `/query` | Per-item sentiment · % of evidence answering a question |
| `POST /assistant/transcribe` · `/ask` · `/speak` | Voice assistant: audio → words · words + screen → reply + actions · reply → MP3 |
| `GET /alerts/recent` · `/entities/{company}/alerts` | Live alerts with affected companies |
| `GET/POST/PUT/DELETE /projects` | Saved analyses |

Interactive docs are served at `http://localhost:8000/docs` while the API runs.

## Tests

```bash
uv run pytest                 # backend; set SEC_CONTACT_EMAIL for the SEC client tests
cd frontend && npm test       # frontend
```

Tests never call the network, a real model or a real database: HTTP uses mock
transports and tables use SQLite.

## Roadmap

- Backtest alert thresholds on historical Kalshi/Polymarket data (false-positive
  rate, does the move lead the stock).
- An LLM explainer that writes the "why it moved" summary for each alert from
  the matched news and posts.
- Trending cards: a ranked feed of the day's biggest market moves by sector.
- User accounts, watchlists and push notifications.

## Security note

The API has no authentication and is meant for local use. Add auth before
exposing it on a network. Keys live only in `.env`, which is gitignored.
