# Trending Cards

**Status:** Draft

## Problem / Why

Raw aggregator and prediction-market data isn't digestible on its own — a
user wants a quick, summarized explanation of what's currently moving and
why. Trending Cards turns classified news/Twitter items and prediction
market data into short, recent summaries explaining market trends.

## Goals

- Surface a feed of "trending" cards, each summarizing recent, relevant
  context for a company, industry, or market.
- Pull from both content aggregators (via [[jev-classifier]]'s output) and
  the prediction-market fetchers ([[ingestion/polymarket]],
  [[ingestion/kalshi]]).

## Non-Goals

- Deep historical backtesting/analysis — that's closer to
  [[display-charting]]'s lead/lag use case.
- Defining the graph used to relate companies — that's
  [[company-network]]; this spec only consumes its output.

## User Stories / Example Interactions

- As a user, I want a card that tells me "NVDA is trending because of
  [summarized news], and Polymarket's odds on [related market] just
  shifted."
- As a user, I want cards for both individual companies and broader
  industries.

## Functional Requirements

1. Pull recent classified content items (from [[jev-classifier]]) and
   recent prediction-market moves (from [[ingestion/polymarket]] /
   [[ingestion/kalshi]]) for a given entity or industry.
2. Generate a short summary card explaining the current trend/context.
3. Use [[company-network]] to pull in related-company context where
   relevant (e.g. a supplier's card referencing the related company's
   news).

## Design / Approach

Left light. Likely reads off whatever store sits behind ingestion/
classification (undecided — see [ingestion overview](./ingestion/README.md))
and generates summaries via an LLM call over the pulled context.

## Interfaces / Data Model

Draft shape (not final):

```
{
  "entity": "NVDA" | "<industry name>",
  "summary": "<generated text>",
  "source_item_ids": ["..."],
  "related_market_ids": ["..."],
  "generated_at": "<ISO 8601>"
}
```

## Dependencies

- [[jev-classifier]] — classified content items.
- [[ingestion/polymarket]] / [[ingestion/kalshi]] — market data.
- [[company-network]] — related-entity context.

## Open Questions

- What counts as "trending" — a fixed refresh cadence, a threshold on
  volume/classification results, or something else?
- Per-entity cards only, or also per-industry/per-cluster (tying into
  [[company-network]]'s cluster-size filters)?
- Summarization approach/model — same as or different from
  [[jev-classifier]]?

## Acceptance Criteria

- A manufactured spike in classified news/market activity for a test
  entity produces a visible trending card within a demo-able time window.
