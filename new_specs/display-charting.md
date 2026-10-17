# Display Charting

**Status:** Draft

## Problem / Why

The project's core goal is comparing prediction-market behavior against
actual stock movement. A chart that overlays the two directly is the most
direct way to let a user (or the system, for lead/lag testing) see that
correlation.

## Goals

- Display a stock price chart for a given ticker.
- Overlay a prediction-market chart (price/odds over time, from
  [[ingestion/polymarket]] or [[ingestion/kalshi]]) on the same timeline.
- Support visually comparing the two to spot agreement, divergence, or
  lead/lag.

## Non-Goals

- Computing a formal lead/lag statistic — this spec is the visualization;
  any quantitative "does the market lead the stock" test is a separate,
  not-yet-specified analysis step (see Option 1's "test if markets lead or
  lag stock prices").
- Marking individual news events on the chart — that's [[news-graphing]].

## User Stories / Example Interactions

- As a user, I want to pick a stock and an overlapping prediction market
  and see both plotted on the same timeline.
- As a researcher, I want to visually spot where the prediction market
  moved before or after the stock did.

## Functional Requirements

1. Render a stock price chart for a selected ticker over a selected time
   range.
2. Overlay a selected prediction market's price/odds series on the same
   chart/timeline.
3. Support both live and historical ranges.

## Design / Approach

Left light. Stock price source resolved: [[ingestion/alpaca]] (free Basic
plan — documented API, 15-minute delay on recent data accepted as a
tradeoff).

## Interfaces / Data Model

Consumes:
- Stock price series from [[ingestion/alpaca]].
- Market price/odds series from [[ingestion/polymarket]] /
  [[ingestion/kalshi]] (see their draft time-series shape).

No new storage of its own beyond what ingestion already provides, unless
chart-specific caching is added later.

## Dependencies

- [[ingestion/alpaca]] — stock price series.
- [[ingestion/polymarket]] / [[ingestion/kalshi]] — market overlay data.
- [[news-graphing]] — layers event markers on top of this chart.

## Open Questions

- How does a user pick "the right" market to overlay for a given
  ticker — manually, or via [[company-network]] once that exists?
- Does this need real-time updates, or is a periodic refresh enough for a
  demo?

### Resolved

- Stock price source: [[ingestion/alpaca]] (free Basic plan, 15-minute
  delay on recent data accepted).

## Acceptance Criteria

- A selected ticker's price chart renders with a selected prediction
  market's price/odds overlaid on the same timeline, within a demo-able
  time window.
