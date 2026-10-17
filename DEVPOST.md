# Devpost submission text: What'sPoppin

## Elevator pitch (≤200 chars)

Prediction markets price the news before stocks do. What'sPoppin maps every Kalshi and Polymarket market to the companies it moves, and Poppy, our voice analyst, explains why with sources.

## About the project

### Inspiration

Prediction markets (Kalshi, Polymarket) now price real-world events minute by minute: Fed decisions, tariffs, export controls, elections, earnings. That is an early, real-money read on exactly the risks that move stocks. But there are tens of thousands of markets and nothing links them to companies. An investor holding NVIDIA has no way to know which markets matter to NVIDIA, its suppliers or its competitors, and usually finds out about a move on social media after it is already priced in.

### What it does

**What'sPoppin is a portfolio intelligence agent for event risk: it watches the markets around your companies and explains what changed and why, with every claim traced to a source.**

- **Company graph from SEC filings.** Search any listed company. Its suppliers, customers, partners, competitors and board interlocks are extracted from 10-K/10-Q and insider filings. Every link stores the URL of the filing it was read from; nothing is saved from model memory.
- **Markets that matter.** Every open Kalshi and Polymarket market is tagged with the companies, sectors and themes it affects. Or describe a risk in plain English ("war in the middle east oil supply") and semantic search finds it.
- **Stock vs. odds.** The stock and the market's odds on one timeline, market-adjusted against SPY, with rolling correlation, confidence bands and lead/lag, and an explicit "correlation, not causation" framing. It refuses to show a number with fewer than 25 data pairs.
- **Evidence on the chart.** News articles (and X posts) are classified for sentiment and pinned to the moment they were published. Ask questions over them: "what % of these are positive?"
- **Live alerts.** A detector scores every market every second against its own history (price z-score, volume bursts, whale orders, order-flow imbalance) and raises an alert only on real moves. Each alert is traced through the graph to the companies that may feel it. Example from a live run: a Kalshi market on California gas prices moved, and What'sPoppin mapped it to Chevron, Exxon and Shell.
- **Poppy, the voice analyst.** Talk to the app: "Show me Nvidia", "find markets about rate cuts", "select the first market and open the chart", "what does the chart show?". Poppy sees what's on screen, drives the UI, and answers only with companies, markets and numbers that are actually displayed. It gives information, not trading advice.

### How it fits Money Intelligence

The track asks for agents that do investing research and portfolio intelligence where every number traces back to a source. What'sPoppin is that for event risk:

- **Research analyst:** it reads SEC filings and produces a sourced relationship graph; each edge cites its filing.
- **Portfolio intelligence:** it watches markets around your companies and their supply chain, and explains what changed (the alert), why (the evidence), and who is exposed (the graph).
- **Grounded numbers:** stock bars come from Alpaca, odds from the venues, news from real articles, relationships from SEC filings. Poppy is constrained to the numbers on screen.

### How we built it

- **Backend:** Python, FastAPI, Postgres. Workers stream Polymarket over WebSocket and poll Kalshi's public REST API (the Kalshi key is optional; with one it streams), compute per-market baselines, run the 1-second alert detector, and map alerts to companies.
- **Frontend:** React + TypeScript, with a force-directed company graph, overlay charts and the Poppy dock.
- **Data:** SEC EDGAR, Kalshi, Polymarket, Alpaca (stocks), NewsAPI.

#### Nicrron: all the AI, through one gateway

Every model call goes through the Nicrron API (`api.nicrron.ai`):

- `nicrron/auto` extracts structured relationships from SEC filings, classifies news sentiment, answers yes/no questions over evidence, and is Poppy's reasoning model. Every answer is validated against a schema; bad output is rejected, never guessed.
- `voyage/voyage-finance-2` (through Nicrron) embeds every market event for semantic search, and shortlists candidate companies for each market before the model confirms them. On 20 live markets this cut model calls from 77 to 40 and made tagging about 3× faster (8.7 s to 2.8 s per event) while keeping 88% of the original tags.

#### ElevenLabs: Poppy's ears and voice

- **Scribe** (speech-to-text) transcribes what the user says; the browser detects the end of speech after about 1.3 s of silence.
- **Flash** (text-to-speech) speaks Poppy's reply and narrates what just loaded ("Nvidia's network is ready: 12 linked companies…").
- The ElevenLabs key stays on the server; the browser only talks to our API.

#### PRISM: observability for the agent

- Every Poppy turn and every market search is traced as a run with timed steps: `assistant.turn` = `context.snapshot` (retrieval) → `llm.complete_structured` (model) → one `action.*` step per UI action; `markets.search` = `embeddings.search` (retrieval) → `venue.activity_filter` (tool).
- Each run is sent to PRISM as a trace (`/api/traces`) with its steps as spans (`/api/spans/ingest`), on a background thread so tracing never slows a request. A failed step marks the run failed with its error type, which is what PRISM's Root Cause clusters group on. PRISM's connection health shows every stage green: traces received, trajectories assembled, analysis ready.
- **Knowledge Base:** the NVDA, TSLA and AAPL 10-Ks (the same filings the graph is built from) assigned to our agent, with a run whose retrieval step used them. *(to be shown in the demo)*
- **Root Cause:** our real failure cluster: classification answers from gateway-routed chat models sometimes came back as a bare number (`0.9`) instead of the expected object, so strict parsing rejected them (`JevError: answer is not an object`). Why it happens: *gateway-routed chat models don't always follow the expected answer format, so strict parsing rejected answers that were otherwise correct.* *(cluster to be shown in the demo)*
- **Evaluator:** "the classifier returns a valid, usable answer for the market", scored on v1 (strict parser) vs v2 (normalizing parser) on the same markets: the failure, the fix, and the score that proves it. *(scores to be shown in the demo)*

### Challenges we ran into

- **Kalshi without a key.** The live WebSocket needs an API key, so we added a keyless mode that polls Kalshi's public REST API and writes the same rows, only when a quote changes.
- **PRISM integration.** Our first exporter used a Bearer token and a raw payload and was rejected; PRISM expects the `X-PRISMtrace-Key` header and its trace/span schema.
- **Chat models vs. strict schemas.** Models routed through a gateway don't always answer in the requested shape; we normalize the common shapes instead of rejecting correct answers (the Root Cause story above).
- **Honest statistics.** Raw price correlation is misleading, so we use SPY-adjusted returns and logit odds changes, show confidence bands, and refuse to compute on thin data.

### Accomplishments that we're proud of

- A company graph where every edge cites a real SEC filing.
- An alert pipeline that runs end to end on live data: move detected, mapped to companies, shown on the graph.
- A voice analyst that can drive the whole app and refuses to invent numbers.
- 673 backend tests and 123 frontend tests.

### What we learned

- Prediction markets are a genuinely early signal, but only useful once they are mapped to companies.
- Grounding beats prompting: constraining the model to sources and validating every answer did more for reliability than prompt tweaks.
- Observability changes how you debug agents: seeing every step of a run makes failures obvious.

### What's next

- Backtest the detector on historical Kalshi and Polymarket data (false-positive rate, and whether moves lead the stock).
- Watchlists and push alerts for your own portfolio.
- Kalshi streaming with an API key, and more venues.

## Built with

python, fastapi, postgresql, sqlalchemy, react, typescript, vite, nicrron, voyage-ai, elevenlabs, prism, sec-edgar, kalshi, polymarket, alpaca, newsapi, websockets

## Try it out

- GitHub: https://github.com/7dracoder/WhatsPoppin

## Additional info: Project Description

What'sPoppin is a portfolio intelligence agent for event risk (Money Intelligence track). Prediction markets (Kalshi, Polymarket) price news before stocks react, but nothing links those tens of thousands of markets to companies. What'sPoppin does: search a company and it builds the company's supplier/customer/competitor/board graph from SEC filings (every link cites its filing URL), finds the prediction markets that matter to it and its neighbours, charts the stock against the market's odds (SPY-adjusted, rolling correlation, lead/lag, "correlation, not causation"), pins sentiment-classified news to the timeline, and raises live alerts when a market makes an unusual move, traced through the graph to the companies exposed. Poppy, a voice analyst, drives the whole app by speech and only cites numbers that are on screen; it gives information, not trading advice.

How it fits Money Intelligence: a research analyst (SEC filings in, sourced relationship graph out) plus portfolio intelligence (watches the markets around your companies and explains what changed, why, and who is exposed), with every number traceable to SEC, Alpaca, Kalshi/Polymarket or real news articles.

Nicrron: all AI runs through the Nicrron API. nicrron/auto extracts relationships from filings, classifies sentiment, answers questions over evidence and is Poppy's reasoning model, with every answer schema-validated. voyage/voyage-finance-2 via Nicrron powers semantic market search and an embedding shortlist that cut model calls from 77 to 40 and made tagging ~3x faster while keeping 88% of tags.

ElevenLabs: Scribe transcribes the user's voice, Flash speaks Poppy's replies and narrates what loaded. The key stays server-side.

PRISM: every Poppy turn and market search is traced as a run with timed steps (retrieval, model call, tool/UI actions) and sent to PRISM as a trace plus spans; failed steps carry their error type for Root Cause clustering. PRISM connection health is fully green. In the demo we show the Knowledge Base (the NVDA/TSLA/AAPL 10-Ks the graph is built from) used by a retrieval step, the Root Cause cluster (chat models returning a bare number instead of the expected object, so strict parsing rejected correct answers), and our evaluator scoring v1 (strict parser) vs v2 (normalizing parser) on the same inputs: the failure, the fix, and the score.

Stack: Python/FastAPI/Postgres workers (Polymarket WebSocket, keyless Kalshi REST polling, per-market baselines, 1-second alert detector), React/TypeScript frontend. 673 backend + 123 frontend tests.
