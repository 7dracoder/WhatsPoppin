# News Graphing *(name TBD)*

**Status:** Draft

> The source design doc flags this feature's name itself as unresolved
> ("News Graphing ? (Change name)"). Treat the name as a placeholder.

## Problem / Why

A price/odds overlay ([[display-charting]]) shows *that* something moved,
but not *why*. Marking where specific news events land on the chart (e.g. a
dot at the point a relevant article or post was published) gives a user the
context for a move without leaving the chart view.

## Goals

- Mark points on a [[display-charting]] chart corresponding to relevant
  news/Twitter events.
- Let a user see what event corresponds to a given marker (e.g. on
  hover/click).

## Non-Goals

- Deciding what counts as "relevant" from scratch — relies on
  [[jev-classifier]]'s output to decide which items are worth marking.
- Rendering the base chart itself — that's [[display-charting]].

## User Stories / Example Interactions

- As a user looking at a stock/market overlay chart, I want to see a dot at
  the point a relevant news event happened, and be able to see what that
  event was.
- As a user, I want only relevant events marked, not every single
  ingested item.

## Functional Requirements

1. For a given ticker/time range being charted, pull classified content
   items (from [[jev-classifier]]) relevant to that entity.
2. Place a marker on the [[display-charting]] chart at each relevant
   item's timestamp.
3. Let a user inspect a marker to see the underlying item (headline/text,
   source, link).

## Design / Approach

Left light. Likely layers markers onto whatever charting approach
[[display-charting]] uses, keyed by timestamp + entity match.

## Interfaces / Data Model

Draft shape (not final):

```
{
  "entity": "NVDA",
  "item_id": "<content item id>",
  "timestamp": "<ISO 8601>",
  "label": "<short marker text>"
}
```

## Dependencies

- [[jev-classifier]] — source of which items are relevant/worth marking.
- [[display-charting]] — the chart markers are placed on.
- [[company-network]] — optional, to also surface markers for related
  companies' news on a given chart.

## Open Questions

- Final name for this feature.
- What threshold decides an item is "relevant" enough to mark — any
  classified item mentioning the entity, or only ones crossing some
  significance threshold?
- How to handle marker density when many events cluster in a short window.

## Acceptance Criteria

- A manufactured relevant news item for a charted ticker appears as a
  marker on the chart at the correct timestamp, within a demo-able time
  window.
