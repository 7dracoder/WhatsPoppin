# Ingestion: Stock Prices (Alpaca)

**Status:** Draft

See [ingestion overview](./README.md). [[display-charting]]'s own Open
Questions flagged "which stock price source" as unresolved — this spec
picks Alpaca's Market Data API (free Basic plan) for that role, replacing
an earlier `yfinance`-based draft of this spec.

## Problem / Why

[[display-charting]] overlays a prediction market's price/odds series
against a stock's price series, but no stock price source was ever picked
— SEC EDGAR (named elsewhere in the project) only covers filings, not
price data. Alpaca's free Basic plan was chosen over the unofficial
`yfinance` library for a documented, stable API with a real historical
bars endpoint (IEX feed, 200 calls/min, history back to 2016) — the
accepted tradeoff is that the free tier delays the most recent 15 minutes
of data, so the "Recent" zoom tier won't show truly up-to-the-second
prices.

## Goals

- Given a ticker and a zoom tier (see Functional Requirements), fetch a
  price series for overlay on a [[display-charting]] chart, in the same
  flat point-per-timestamp shape [[ingestion/polymarket]] /
  [[ingestion/kalshi]] already use.
- Support both intraday ranges (recent days, fine granularity) and longer
  historical ranges (coarser granularity), matching whatever window the
  chart is showing.

## Non-Goals

- Live/real-time streaming via Alpaca's websocket feed — this spec is the
  on-demand historical-bars REST path only; no persistent connection like
  [[ingestion/kalshi]]'s WebSocket worker.
- Options, fundamentals, dividends, or anything beyond a close price per
  bar.
- Preserving Alpaca's open/high/low per bar — Alpaca's API returns full
  OHLC, but only the close is kept, to match the flat shape the market
  side uses (see Interfaces/Data Model). Intra-bar detail is dropped on
  purpose, not an oversight.
- Working around the free tier's 15-minute delay — accepted as-is (see
  Problem/Why), not solved here. Upgrading to Algo Trader Plus ($99/mo,
  no delay) would remove it, but isn't planned for the MVP.

## User Stories / Example Interactions

- As a user, I want to pick a ticker on a chart and see its price history
  for whatever range the chart is showing.
- As the system, I want stock price data in the same time-series shape
  prediction-market data uses, so [[display-charting]] doesn't need
  source-specific handling for the two series it overlays.

## Functional Requirements

1. Accept a ticker symbol and a zoom tier (not a raw interval — see the
   tier table below) as input; map the tier to the Alpaca bars-endpoint
   `timeframe`/range it actually needs internally.
2. Fetch OHLCV bars for that ticker/tier via Alpaca's historical bars
   endpoint (IEX feed, free tier) — Alpaca's API itself returns full OHLC
   per bar.
3. Flatten each bar to its `close` price and normalize into the shared
   flat point shape (see Interfaces/Data Model) — the same shape
   [[ingestion/polymarket]] / [[ingestion/kalshi]] already use
   (`source`, `market_id`, `price_or_odds`, `volume`, `timestamp`), so
   [[display-charting]] treats both series identically. Open/high/low are
   discarded here, not carried through.
4. Support these zoom tiers:

   | Tier | Range | Timeframe |
   |---|---|---|
   | Recent | last 6h | 1Min |
   | Daily | last 24h | 5Min |
   | Weekly | last 7d | 1Hour |
   | Monthly | last 30d | 1Hour |
   | All-time | full history (since 2016) | 1Day |

   Monthly reuses the same `1Hour` timeframe as Weekly, just over a wider
   range — no special handling or client-side aggregation needed.
5. Fetch on demand, triggered when a user selects a ticker or changes
   zoom tier on a chart — not continuously polled. Same on-demand design
   principle as [[ingestion/twitter-lookup]], for the same reason: no
   point fetching tickers nobody is looking at.
6. For the Recent tier, surface that the latest ~15 minutes may be
   missing/delayed (free tier limitation) rather than silently showing a
   gap as if it were real-time data.

## Design / Approach

A thin gateway module wrapping Alpaca's historical bars REST endpoint,
following the same `client.py` (gateway) + `normalize.py` (raw → shared
shape) split already used in [[ingestion/news-aggregator]] and
[[ingestion/twitter-lookup]]. `normalize.py`'s job here is specifically
the OHLC-bar → flat-point flattening (keep `close`, drop `open`/`high`/
`low`), not just a field rename. Called directly from whatever endpoint
backs [[display-charting]] — not scheduler-triggered
([scheduler.md](./scheduler.md) only runs the four continuously-polled
sources; this is on-demand like twitter-lookup.md, not one of those four).

## Interfaces / Data Model

Draft shape (not final) — matches [[ingestion/polymarket]]'s flat shape
field-for-field, with `question` dropped (not applicable to a stock
ticker):

```
[
  {
    "source": "alpaca",
    "market_id": "<ticker>",
    "price_or_odds": 0.0,
    "volume": 0,
    "timestamp": "<ISO 8601, UTC>"
  }
]
```

`price_or_odds` is the bar's close price; `volume` is the bar's volume.
Same flat shape [[ingestion/polymarket]] / [[ingestion/kalshi]] already
use, so [[display-charting]] can overlay all three without
source-specific code.

## Dependencies

- Alpaca Market Data API (free Basic plan) — requires an API key/secret
  pair (`APCA-API-KEY-ID` / `APCA-API-SECRET-KEY`), unlike `yfinance`'s
  no-key approach; needs an Alpaca account signup.
- [[display-charting]] — sole consumer; this spec exists to unblock its
  stock-price-source Open Question.

## Open Questions

None open — see Resolved below.

### Resolved

- Stock data provider: Alpaca free Basic plan, accepting the 15-minute
  delay on recent data rather than paying for Algo Trader Plus or relying
  on `yfinance`'s unofficial/unstable scraping.
- Zoom tiers and their range/timeframe mapping — see Functional
  Requirement 4.
- Timezone handling: this module normalizes to UTC ISO-8601 strings
  itself before emitting bars, same convention [[ingestion/kalshi]]
  already uses (`_parse_time`, timezone-aware DB columns) — not pushed
  onto [[display-charting]].
- The market (Kalshi/Polymarket) side of the overlay: same approach as
  this spec — fetch on demand per zoom tier (no new Postgres storage),
  using Kalshi's candlesticks `period_interval` (1/60/1440 minutes) and
  Polymarket's `prices-history` `fidelity`, both of which already map
  onto these same tiers. Not written up as its own spec; implementation
  deferred, but the direction (mirror this file) is settled.

## Acceptance Criteria

- A valid ticker and a zoom tier returns a price series in the shared
  flat shape (`price_or_odds` as the close, no open/high/low), ready to
  render alongside a prediction-market series on a chart, within a
  demo-able time window.
- A Recent-tier request made less than 15 minutes into a trading session
  returns data missing/flagged for that gap, not silently truncated as if
  nothing happened.
