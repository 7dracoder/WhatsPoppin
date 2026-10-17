# Ingestion: Polymarket Fetcher

**Status:** Draft

See [ingestion overview](./README.md) for why this is a market time-series
source rather than a classifiable content item.

## Problem / Why

Polymarket prices real-world events (Fed decisions, inflation prints,
tariffs, elections) in real time. Per the project's core goal, this is one
of the two prediction-market sources whose price/odds history gets overlaid
against actual stock movement to look for agreement, divergence, or
lead/lag.

## Goals

- Let a user browse/select specific Polymarket markets.
- Fetch price/odds and volume data over time for a selected market, for
  overlay on top of stock data in [[display-charting]].
- Support both live and historical data, since lead/lag testing
  (Option 1) needs a price history to backtest against.

## Non-Goals

- Executing trades on Polymarket (read-only fetch only; direct trading is
  blocked for US users anyway).
- Inferring relationships between markets and companies automatically —
  that mapping is [[company-network]]'s job, if built; this spec only
  fetches raw market data.

## User Stories / Example Interactions

- As a user, I want to pick a Polymarket market and see its odds plotted
  alongside the stock price of the company it affects.
- As a researcher, I want historical Polymarket odds so I can test whether
  the market led or lagged the stock move.

## Functional Requirements

1. Let a user look up/select a Polymarket market (by question/topic or by
   linked ticker, once [[company-network]] exists).
2. Fetch price/odds and volume for a selected market via the Polymarket
   public API / Activity Feed.
3. Support both a live/recent pull and a historical pull for backtesting.
4. Emit market data in a time-series shape usable by [[display-charting]]
   and [[trending-cards]].

## Design / Approach

Left light. Likely a fetch-on-demand (per selected market) rather than a
continuous poller of every market, since the use case here is "overlay this
specific market," not "ingest everything." Historical pulls via the
Polymarket API or community tooling, per Option 1's "Data and tools" note.

## Interfaces / Data Model

Draft shape (not final — distinct from the content-item schema):

```
{
  "source": "polymarket",
  "market_id": "<source-native market id>",
  "question": "<market question/topic>",
  "price_or_odds": <number>,
  "volume": <number | null>,
  "timestamp": "<ISO 8601>"
}
```

## Dependencies

- Polymarket public API / Activity Feed.
- [[display-charting]] — primary consumer, for overlay.
- [[company-network]] — if built, used to link a market to the
  company/companies it affects.

## Open Questions

- How does a user (or the system) find the "right" market to overlay for a
  given company — manual selection, or an automated company↔market link
  via [[company-network]]?
- US access: direct trading is blocked for US users — confirm read access
  to market/odds data isn't similarly restricted.
- Historical data depth/availability via the public API vs. community
  tooling.

## Acceptance Criteria

- A selected Polymarket market's price/odds history renders as an overlay
  on a stock chart within a demo-able time window.
