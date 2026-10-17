# Alert → Company Impact

**Status:** Built (backend, tiers 1-3): `backend/alert_impact/`, `backend/api/alerts.py`

## Problem / Why

The alert detector already flags real market moves in real time (`alerts`, announced with
`pg_notify('alerts', id)`). [top-markets-helper.md](./top-markets-helper.md) goes from a
company to its markets. This spec goes the other way: from an alert to the companies it
affects, so a user sees "this market just moved, and these companies are exposed to it" while
it is still news.

Most of the pieces exist. An alert names its market; enrichment tags markets with map
entities; the company graph links companies to each other; the workspace graph already
colours companies with recent events. This spec connects them.

## Goals

- React to each new alert within seconds: find its market, make sure the market's event is
  enriched, and match the event's tags backwards to companies.
- Rank the affected companies by how directly they are tied to the market (same tiers as the
  top markets helper, reversed).
- Store the result so the existing company-graph highlights and the UI pick it up, plus an
  API for an alerts view.

## Non-Goals

- Detecting alerts. The alert detector does that; this only reads `alerts`.
- Predicting price direction or size for a company. A highlight says a company is exposed,
  never which way its stock will move (same rule as company graph F8).
- Researching an alert with news and tweets. That is the LLM enricher `alerts.status` was
  designed for, and it can build on this spec's output.
- New polling, new enrichment logic or a new entity map.

## User Stories / Example Interactions

- As a user with the workspace open, a Kalshi "WTI above $90" market spikes; within seconds
  Exxon and Chevron light up on the graph, with the market and the reason shown in the node card.
- As a user, I open an alerts view and see the last hour's alerts, each with its top affected
  companies and why ("tagged: Crude Oil", "linked to XOM: supplier").
- As a user looking at Nvidia, I see the recent alerts that affected it, newest first.

## Functional Requirements

1. Pick up every new alert, by `LISTEN alerts` with a catch-up query on start (alerts written
   while the worker was down).
2. Find the alert's market: `alerts.(source, market_id)` → `markets` → its `event_id`.
3. Make sure the event is enriched under the current map version. If it is, reuse its tags; if
   not, enrich that event now, ahead of the backlog (one Jev run, ~2 s), with the same code and
   tables as the enrichment worker.
4. Match backwards from the event's tags to companies, in tiers (see Design), keeping up to
   `N` (default 10) per alert.
5. Store one impact row per (alert, company), and the company-graph event so highlights and
   the graph pick it up.
6. A failure (Jev down, market unknown) is recorded and retried, and never blocks the next
   alert.
7. Never claim or change `alerts.status`; that column belongs to the future LLM enricher.

## Design / Approach

### What already exists

| Piece | Where | Used for |
|---|---|---|
| `alerts` (+ `pg_notify('alerts', id)`) | `alert_detector/db.py`, `backend/models/alert.py` | Trigger; `source`, `market_id`, `event_id`, `direction`, `summary`, `created_at` |
| `markets` | ingestion | Title, event, close time, URL |
| Event enrichment | `backend/enrichment/enrich.py` `enrich_event`, `db.save_result`, `db.sibling_tags` | Tag the alert's event on demand |
| `market_entities`, `entities` | market-search | The event's tags and their categories |
| `entity_relationships` | company graph | Companies linked to a tagged company |
| `graph_events` (`source='market'`, `event_type='odds_move'`, `alert_id`) | company graph F7 | Feeds F8 highlights and the workspace graph's "recent highlight" colouring |
| `rerank` pattern | `backend/enrichment/rerank.py` | Optional Jev scoring |

### Backwards matching

From the event's tags (`market_entities` for its markets, map entities only):

| Tier | Company | Why |
|---|---|---|
| 1 | Company entities in the tags | Enrichment already asked Jev "is this market about this company, or would its outcome directly affect it?" for every map company in a chosen category |
| 2 | Companies linked by `entity_relationships` (filing links, either direction) to a tier-1 company | A supplier, customer, partner or competitor of a directly affected company |
| 3 | Companies whose `company_entity_tags` include one of the event's resource, country, sector or event tags | e.g. a Crude Oil market → every company whose F14 relationship keywords ("oil", "crude") resolved to Crude Oil |

Within a tier: more matched tags first, then the company graph's link `confidence` (tier 2).
Optional `rerank`: one Jev call scoring "would this market's move matter to an investor in
{company}?" for every candidate company, drop under 0.5. It mirrors `rerank.py`, with
companies as the candidates instead of markets.

Tier 3 reads `company_entity_tags` in reverse (entity -> companies): company graph F14's
free-text relationship keywords, resolved once to map entities by `match_tags` and cached (see
[top-markets-helper.md](./top-markets-helper.md)). It is what reaches companies two steps away,
such as a memory-chip market reaching a company whose supplier edge is tagged "memory". Until
F14 has run and the cache is filled, tier 3 is empty and an oil market only reaches the
companies Jev tagged directly in tier 1.

### Tier 3 as built: Jev over every company

Instead of F14 keywords, tier 3 asks Jev directly (`backend/alert_impact/jev_match.py`): the
market's title, outcome, event and rules are the state, and every company in `entities` (the 50
map companies plus those the company graph added, 121 today) gets one yes/no question, "would
this market's outcome matter to an investor in X: is it about X, or would it directly move its
sales, costs, prices or regulation?", 50 per call, calls in parallel (~1-2 s). Companies at 0.5
or more that tiers 1-2 didn't already find are tier 3, ranked by probability, stored with
`relevance` and the market's own tags as `matched`. This reaches companies from markets about
things (gas prices -> refiners) with no keyword table. If Jev fails, the alert keeps its tiers
1-2 and the run records why tier 3 was skipped. `ALERT_IMPACT_JEV=0` turns it off. The F14 /
`company_entity_tags` route below remains an option for cheaper, explainable matches.

### Worker

`python -m backend.alert_impact`, next to the enrichment worker:

1. On start: process alerts from the last `ALERT_IMPACT_LOOKBACK_MIN` (default 60) with no
   `alert_impacts` row. Then `LISTEN alerts` (asyncpg listener); each notification is an alert
   id.
2. Per alert: load alert + market; `sibling_tags` (already tagged) or `enrich_event` +
   `save_result` (tag now); backward match; save.
3. Demo alerts (`series_id = 'KXDEMO'`, from `alert_detector.demo`) are tagged on demand too,
   so a demo produces impacts; normal enrichment skips that series.

Latency target: alert written → impacts stored in under 5 s (under 1 s when the event is
already tagged).

## Interfaces / Data Model

New table **`alert_impacts`** (one row per alert and company):

| column | type | notes |
|---|---|---|
| `alert_id` | `BIGINT` (PK part) | `alerts.id`, no foreign key (reset_db drops `alerts` with CASCADE) |
| `entity_symbol` | `TEXT` (PK part) | The affected company |
| `tier` | `INT` | 1, 2 or 3 |
| `matched` | `TEXT[]` | The tags or linked company that made it match, for "why" text |
| `relevance` | `NUMERIC` | Jev score when reranked, else null |
| `created_at` | `TIMESTAMPTZ` | |

Plus a status row per alert for retries, either in `alert_impacts` itself (a row with a null
company) or a small `alert_impact_runs (alert_id, status, error, attempts)`.

Also writes `graph_events` for tier-1 companies (one per company and market URL, exactly like
F7), so F8 and the workspace graph need no change. Tier-2 companies get no `graph_events` row:
F8 already spreads a company's events to the companies it links to, so writing them too would
double that.

API (draft, in `backend/api/alerts.py`):

- `GET /alerts/recent?since=&limit=`: recent alerts with their market and top impacts.
- `GET /entities/{company_id}/alerts?since=`: alerts that affected one company, newest first.
- Optional live push: `GET /alerts/stream` (server-sent events, fed by the same `LISTEN`).

## Dependencies

- Alert detector running (and its baselines), writing `alerts`.
- Enrichment code and tables ([market-search.md](./market-search.md)), Jev via the Nicrron gateway
  with credits.
- Company graph: `entity_relationships`, `graph_events` (F7), highlights (F8).
- Company graph F14 (`company_graph/tags.py`, `entity_relationships.tags`) and the
  `company_entity_tags` cache from [top-markets-helper.md](./top-markets-helper.md), for tier 3.

## Open Questions

- **UI (main decision).** Options, cheapest first:
  1. Graph only: the stored `graph_events` make affected companies light up on the existing
     workspace graph, with the alert in the node card. No new frontend.
  2. Per-company list: the node card or company page shows `GET /entities/{id}/alerts`.
  3. Alerts feed panel: a live list (`/alerts/recent` polled, or `/alerts/stream`) where each
     alert expands into its affected companies and clicking one opens it on the graph.
  Recommended: 1 now (free), then 3 for the demo, since a live feed is the most visible.
- **F7 overlap.** F7 already maps alerts to companies by name. Run both (graph_events dedupe on
  company + URL), or replace F7's name matching with this?
- **F14 coverage.** F14 is a one-shot script and only tags edges that exist; how many
  companies have tags today, and should the cache step run automatically after each link run?
- **Direction.** Show the alert's direction (`yes_up` / `yes_down`) as a plain fact next to the
  impact, or leave direction entirely to F8?
- **Volume of alerts and Jev cost.** One Jev run per newly seen event, plus one rerank call per
  alert if enabled. Cap the rerank, or only rerank alerts above a score?
- **Who claims `alerts.status`.** This spec leaves it alone; confirm with the alert detector
  owner that the future LLM enricher still owns it.

### Resolved

- Starts from the alert detector's alerts; does not detect anything itself.
- Reuses event enrichment for the alert's market, on demand, instead of a new classifier.
- Matching is the top-markets tiers reversed.
- Tier 3 comes from company graph F14's relationship keywords, resolved to map entities and
  cached, not a hand-written list.
- Builds on existing tables; one new table for impacts.

## Acceptance Criteria

- A new alert on an already-tagged market produces `alert_impacts` rows within 1 s; on an
  untagged market, within 5 s, and the event is then tagged for search too.
- A Crude Oil alert lists companies Jev tagged directly (tier 1) above their graph-linked
  companies (tier 2), each with `matched` set.
- Every tier-1 company also has a `graph_events` row with the alert's id, so it feeds the
  workspace graph's highlights without frontend changes.

Verified live (2026-10-10, shared DB): a test alert on "NVIDIA B200 · Average hourly price in
July 2027", inserted with the detector's own `insert_alert` (so `pg_notify` fired), was
processed in 0.6 s: NVDA tier 1 with a `graph_events` row; COHR, CRWV, FN, GOOGL, INTC, IREN,
MSFT, MU, NBIS tier 2; served by `/alerts/recent`, `/entities/NVDA/alerts` and
`/entities/MU/alerts`. The test rows were deleted afterwards. Tier 3 (F14 keywords via
`company_entity_tags`) is not built yet.
- An alert written while the worker was down is processed on restart (within the lookback).
- A Jev or database failure for one alert is recorded and retried, and the next alert is still
  processed.
- `alerts.status` is never changed.
