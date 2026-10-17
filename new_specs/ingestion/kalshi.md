# Ingestion: Kalshi Fetcher

**Status:** Draft

See [ingestion overview](./README.md) and [polymarket.md](./polymarket.md) —
this spec mirrors Polymarket's shape 1:1, as a second prediction-market
source for the same overlay use case.

## Problem / Why

Kalshi is the second prediction-market source named alongside Polymarket in
the project's core goal (mapping prediction markets against stock
movement). Keeping it in the same shape as [[polymarket]] means
[[display-charting]] and [[trending-cards]] don't need source-specific
logic.

## Goals

- Let a user browse/select specific Kalshi markets.
- Fetch price/odds and volume data over time for a selected market, for
  overlay on top of stock data — same use case as [[polymarket]].

## Non-Goals

- Executing trades on Kalshi.
- Anything beyond basic market/price data capture — no relationship
  inference here.

## User Stories / Example Interactions

- As the system, I want Kalshi market data available in the same shape as
  Polymarket's, so [[display-charting]] doesn't need source-specific
  handling.

## Functional Requirements

1. Let a user look up/select a Kalshi market.
2. Fetch price/odds and volume for a selected market via Kalshi's public
   API.
3. Emit market data in the same time-series shape as [[polymarket]].

## Design / Approach

Not yet designed in detail — mirrors [[polymarket]]'s approach.

## Interfaces / Data Model

Same draft shape as [[polymarket]], with `"source": "kalshi"`.

## Dependencies

- Kalshi public API.
- [[display-charting]] — primary consumer, for overlay.

## Open Questions

- Does Kalshi's public API offer comparable price/odds/volume visibility to
  Polymarket's?
- Is Kalshi actually in scope for the MVP, or is Polymarket alone enough to
  demo the overlay concept?

## Acceptance Criteria

- A selected Kalshi market's price/odds history renders as an overlay on a
  stock chart within a demo-able time window.
