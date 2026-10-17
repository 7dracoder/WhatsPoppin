# Top Markets Helper

**Status:** Built (backend): `GET /entities/{company}/top-markets`, `backend/enrichment/tag_match.py`

## Problem / Why

Given a company, the product needs the few prediction markets that matter most to it: the
markets about the company itself first, then markets about the things it is tied to (the
oil it depends on, the countries it sells into, its sector, its close partners and rivals).
[market-search.md](./market-search.md) already tags every market with entities from the
predefined map, so this is one ranked query over those tags, not a new pipeline.

## Goals

- One helper and one query: company in, top 3 markets out, across Kalshi, Polymarket and
  Polymarket US.
- Match markets against the company and its tags, ranking by relevance first and market
  activity second.
- Define where a company's tags come from.

## Non-Goals

- New enrichment, polling or classification. The helper only reads what market-search and
  the company graph already store.
- Ranking by price movement or alerts.
- Frontend work.

## User Stories / Example Interactions

- As a user looking at Exxon, I see Exxon's own markets first, then crude oil markets, then
  energy-sector markets.
- As a user looking at a company with no market of its own, I still see the markets for the
  things it is tied to.
- As a frontend developer, I call one endpoint and get at most 3 market cards in order.

## Functional Requirements

1. Given a company (an `entities.symbol` with `type = 'company'`), return up to 3 active
   markets, ranked as in Design.
2. Match a market when any of its `market_entities` rows is the company or one of the
   company's tags.
3. Return at most one market per series (a market without a series: per event). Tags are
   stored per event, so every strike of an event matches equally, and a series repeats one
   question every month; without this rule, the top results are one question many times
   (seen live: 9 of NVDA's top 10 were "NVIDIA A100/H100/H200/B200 average hourly price" months).
4. When fewer than 3 events match, return only the ones that do (0, 1 or 2) and nothing else.
   Never pad with unrelated markets. Zero matches is an empty list, not an error.
5. Unknown company -> 404.

## Design / Approach

### What already exists (checked in the code)

| Table | Fields used | Notes |
|---|---|---|
| `entities` | `symbol`, `name`, `type` | The map: company, country, sector, event, resource, person |
| `market_entities` | `source`, `market_id`, `entity_symbol` | Tags per market, indexed by `entity_symbol` |
| `markets` | `source`, `market_id`, `event_id`, `title`, `outcome_label`, `status`, `close_time`, `url`, `category`, `series_title` | No volume or open interest |
| `entity_relationships` | `entity_symbol`, `related_entity_symbol`, `relationship_type`, `confidence`, `source` | Company graph edges: supplier, customer, partner, competitor, sector_peer. Companies only |
| `market_prices` | `volume`, `open_interest` | Only streamed markets (Kalshi top 1,000 by volume, Polymarket top N), kept 30 minutes |
| `market_hourly` | `notional` ($ traded per hour) | Only streamed markets, kept 14 days |

### Where a company's tags come from

1. **The company itself**: its own `entities` row.
2. **Close companies**: `entity_relationships` edges in either direction (read through the
   company graph's `read_links`, which flips reverse-lookup edges), filing-sourced
   (`source = 'filing'`) only. `sector_peer` edges are left out: they are a weak SIC-code
   fallback, and the sector itself is covered by tag 3.
3. **Resources, countries and sectors**: from company graph F14 (`company_graph/tags.py`).
   F14 stores up to 5 free-text keywords on each `entity_relationships` row ("oil", "memory",
   "strait of hormuz"), and `get_company_tags(session, symbol)` returns every keyword on any
   edge naming the company. Those keywords are free text, so they go through `match_tags`
   (exact, else one Jev call) to become map entities, the only thing markets are tagged with.
   The result is cached in `company_entity_tags` (below) so neither a search nor an alert pays
   for that Jev call. This replaces the hand-written `company_tags` list proposed earlier.

Callers may also pass tags (`tags=`), as free text: "ev", "electric vehicle", "microchips".
`backend/enrichment/tag_match.py` turns each into a map entity: exact name, ticker or alias
matches are free; the rest go to Jev in one call (a Choice question per tag per half of the
map, plus "none of these"), kept at probability 0.5 or more. Unmatched tags are returned in
`unmatched_tags`. Matched tags rank as tier 3.

### Ranking: relevance first, activity second

Each matching event gets the score of its best tag:

| Tier | Match | Example (XOM) |
|---|---|---|
| 1 | The company itself | "Will Exxon beat Q4 earnings?" |
| 2 | A close company (graph edge) | "Will Chevron complete the Hess deal?" |
| 3 | A resource, country or sector tag | "WTI above $90 on Dec 31?" |

Within a tier, more matched tags rank higher, then the activity tiebreaker. Of an event's
markets, the one returned is the most active (or the soonest to close).

**Tiebreaker: what we store and what is missing.**

- Not stored on `markets`: 24h volume, total volume, open interest.
- The pollers already have these in hand during discovery and drop them: Kalshi
  (`volume_24h_fp`, `volume_fp`, `open_interest_fp`) and Polymarket (`volume24hr`). Polymarket
  US only reports `sharesTraded` and `openInterest` on its live stream, not at discovery.
- `market_prices` and `market_hourly` have volume, but only for the ~1,500 streamed markets
  (out of ~19,000 Kalshi markets alone), so they can't rank the rest fairly.

**Proposal:** add a nullable `volume_24h` column to `markets`, written by the Kalshi and
Polymarket upserts (`ALTER TABLE markets ADD COLUMN IF NOT EXISTS volume_24h numeric` on
worker start, since `create_all` doesn't add columns to an existing table). Rank by
`volume_24h DESC NULLS LAST`, then `close_time ASC`. Until the column exists, the helper
ranks by `close_time ASC` alone (soonest-resolving first). Open interest is a better
"money at stake" measure, but Polymarket has no equivalent at discovery, so 24h volume is the
one measure available across sources.

### Optional Jev rerank (`rerank=true`)

The tag query's top `candidates` events (default 50) go to Jev in one call per 50: state is
the company and what it is tied to; one yes/no question per event, "would this market's
outcome matter to an investor in the company?". Events scoring 0.5 or more are returned,
most relevant first, with `relevance` set. Measured live on 5 companies (100 tagged events):
about 1 s per search either way and a fraction of a cent more per search; it moved
Fed-decision markets to the top for JPM over a Freddie Mac IPO, and natural gas and crude
markets for XOM over an ExxonMobil credit-card-spend market (relevance below 0.5).

### Query shape

One SQL query: the company's tag list (with tier per tag) joined to `market_entities`, then
`markets` (`status = 'active'`), grouped by `(source, coalesce(event_id, market_id))`, scored
`(min tier, -matched tags, -volume_24h, close_time)`, `DISTINCT ON` the event, `LIMIT 3`.
Building the tag list (seed + one `read_links` call) happens before the query.

## Interfaces / Data Model

Helper (Python, next to the other search reads in `backend/enrichment/db.py`):

```python
async def top_markets(engine, company_symbol: str, extra_tags: list[str] = (), limit: int = 3) -> list[dict]
```

Endpoint, mounted with the entity routes in `backend/api/entities.py`:

`GET /entities/{company_id}/top-markets?limit=3&tags=ev&tags=lithium`

```json
{
  "company": {"id": "XOM", "name": "Exxon Mobil Corporation", "category": "company"},
  "tags": [{"id": "Crude Oil", "category": "resource", "source": "map"},
           {"id": "CVX", "category": "company", "source": "graph"}],
  "markets": [
    {"source": "kalshi", "market_id": "...", "event_id": "...", "title": "...",
     "outcome_label": "...", "url": "...", "close_time": "2026-12-31T00:00:00Z",
     "volume_24h": 12345.0, "matched": ["Crude Oil"], "tier": 3}
  ]
}
```

`matched` and `tier` let the UI say why a market was picked, which the frontend design asks
for. Schema changes: the `volume_24h` column (proposed) and the `company_entity_tags` cache.

**`company_entity_tags`** (new, small cache of F14 tags resolved to map entities):

| column | type | notes |
|---|---|---|
| `company_symbol` | `TEXT` (PK part) | The company (`entities.symbol`) |
| `entity_symbol` | `TEXT` (PK part) | A map entity its F14 keywords resolve to |
| `keywords` | `TEXT[]` | The F14 keywords that resolved to it, for "why" text |
| `resolved_at` | `TIMESTAMPTZ` | |

Filled by a one-shot step after `python -m company_graph.tags` (same pattern as F14):
`python -m backend.enrichment.company_tags` resolves every company whose F14 keywords
changed since `resolved_at`. Indexed both ways: company -> entities (top markets) and
entity -> companies (alert impact).

## Dependencies

- [market-search.md](./market-search.md): `entities`, `market_entities`, entity map.
- Company graph: `entity_relationships` and `read_links`.
- Kalshi and Polymarket pollers, for the proposed `volume_24h` column.

## Open Questions

- **Volume column**: OK to add `volume_24h` to the shared `markets` table and the two pollers?
  Without it, ties are broken by close time only.
- **Tag matching consistency**: Jev sees all of a request's tags together, which can shift a
  pick (seen live: "microchips" matched Semiconductors alone, Computer Memory Chips next to
  "memory chips"). One call per tag would be steadier at a few more calls.
- **Companies outside the map**: any graph company with F14 tags gets tier-3 entities, but
  only the 50 map companies can be tagged on markets directly (tier 1). Return their graph-based matches only, or
  extend the map?
- **One market per event**: return the event's most active market, or an event-level card
  (question + all outcomes)?
- **Limit**: the frontend card picker shows ~10 markets; keep 3 as the default with `limit`
  up to 10?

## Acceptance Criteria

- For a company with its own tagged markets, the first result is tagged with the company.
- A market matching only a resource/country/sector tag never ranks above one tagged with the
  company or a close company.
- No two results come from the same series (or, without one, the same event).
- A company with 2 matching events returns exactly 2; with none, an empty list and HTTP 200.
- Every result is active (`status = 'active'`) and carries `matched` and `tier`.
- One SQL query per request, after the tag list is built.

