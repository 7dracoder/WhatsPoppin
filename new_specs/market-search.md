# Market Search & Entity Enrichment

**Status:** Built (backend). Implementation notes: `backend/enrichment/enrichment.md`

## Problem / Why

Prediction markets open constantly across Kalshi, Polymarket, and
Polymarket US, but there's no easy way to find every market related to a
given topic, company, or entity. We already poll all three for new
markets. Enriching each market with Jev to extract its entities lets
users search by any entity and see every related market, and the same
entities feed a graph database so we can map relationships between
markets and entities.

## Goals

- Define a predefined map of entity categories and the entities that
  fall under each. The guiding principle: capture anything that can
  affect a company. Categories:
  - Companies
  - Countries
  - Sectors
  - Events
  - Resources
  - People
- Build a full pipeline that takes newly polled markets from Kalshi,
  Polymarket, and Polymarket US, sends them to Jev for enrichment, and
  assigns entities from the predefined map.
- Store enriched markets and their entities in a new table that can be
  queried by entity or category.
- Backend API for entity autocomplete and entity → markets lookup, ready
  for a future frontend search bar to plug into.

## Non-Goals

- Graph database of market/entity relationships (follows later, built on
  the stored entities).
- Semantic / vector search (considered and dropped for v1).
- Entity aliases (e.g. "Musk" → Elon Musk) for autocomplete.
- Frontend / search bar UI. There's no frontend yet; this spec builds
  the infrastructure so one can plug in later.

## User Stories / Example Interactions

- As a user (via a future frontend), I type an entity (e.g. "Tesla") into
  a search bar and see every related market across Kalshi, Polymarket,
  and Polymarket US.
- As a frontend developer, I can call a documented API for autocomplete
  and results without touching the pipeline or database.

## Functional Requirements

1. Expose an autocomplete endpoint that returns matching entities from
   the predefined map for a partial query (e.g. "Tes" → Tesla).
2. Expose a results endpoint that returns every market linked to a
   given entity across all three sources.
3. The enrichment worker processes unenriched markets through the
   two-pass Jev flow and writes entity assignments and status.

## Design / Approach

- Jev is constrained to choose from the predefined entity map, so it
  never produces entities outside the map.
- v1 map size: ~50 hand-picked entities per category (~300 total).
- The map lives in a hand-written seed file in the repo
  (`backend/entities/data/entity_map.json`, grouped by category) and is
  loaded into the `entities` table. Companies are not repeated there: the
  file points at the existing `sp500_top50.json`, the list the matcher and
  company graph already use. Changes to either file go through normal code
  review.
- The seed file carries a `map_version`. Each enrichment records the
  version it ran under; when the version is bumped, the worker
  re-enriches only markets with an older `map_version`.
- Enrichment is per event, not per market: an event is one question with one
  resolution date, and its markets are the yes/no contracts in it (strikes,
  ranges, candidates). One Jev run tags the event and every market in it gets
  the same tags; a market added later to a tagged event copies them. This keeps
  Jev cost proportional to events (~1,600 across the followed Kalshi categories)
  rather than markets (~19,000).
- Two-pass enrichment per event, both through
  `backend.classification.classify` in `multi_select` mode:
  1. Jev picks which of the 6 categories apply.
  2. For each chosen category, Jev picks which entities apply from that
     category's ~50 options.
- Input is the existing `markets` table, which is already normalized
  across Kalshi, Polymarket, and Polymarket US. This spec adds no
  cleaning step of its own.
- Enrichment runs in a separate worker (`python -m backend.enrichment`),
  decoupled from polling. Pollers only insert raw markets; the worker
  picks up markets not yet enriched, sends them to Jev, and writes the
  results. Jev slowness/outages never stall polling, and failures are
  retried.

## Interfaces / Data Model

Built on the existing tables rather than new copies of them:

- **`entities`** (existing, db-design.md table 1): the map is inserted
  here. `symbol` is the id (a ticker for companies, the name itself
  otherwise) and `type` is the category: `company` | `country` | `sector`
  | `event` | `resource` | `person`.
- **`market_entities`** (existing): one row per
  `(source, market_id, entity_symbol)`, the key of `markets`. Company
  graph F7 also writes here; re-enrichment replaces only links to map
  entities.
- **`market_enrichment`** (new): enrichment status per market.
  - `source`, `market_id` (PK, same key as `markets`, no foreign key since
    `reset_db` drops `markets` with CASCADE)
  - `status` (pending / done / failed), `enriched_at`, `error`
  - `map_version`: seed file version used for this enrichment
  - `attempts`: failures in a row under that version (retries stop at 3)

API endpoints (`uv run uvicorn backend.main:app`, port 8000):

- `GET /entities/autocomplete?q=<partial>[&category=][&limit=10]`:
  matching map entities (`id`, `name`, `category`), case-insensitive
  prefix match on name or ticker.
- `GET /entities/{id}/markets[?limit=200]`: the entity and all markets
  linked to it, across Kalshi, Polymarket, and Polymarket US.

## Dependencies

- Existing pollers and the `markets` table (Kalshi, Polymarket,
  Polymarket US).
- Jev API via `backend/classification` (OpenRouter).
- Postgres.

## Open Questions

- Per-call question limit for Jev: pass 2 asks up to ~50 yes/no
  questions in one call. Untested against the live API at the time of
  writing.

### Resolved

- Feature scope: market search & entity enrichment.
- Jev chooses only from the predefined map, never produces new entities.
- Jev multi-select: Choice is single-select only, so both passes use
  `multi_select` (one yes/no question per option, one call per pass).
- Enrichment runs in a separate worker, not inline with polling.
- No cleaning step; input tables are already normalized.
- Search is autocomplete over entity names; semantic search dropped.
- No frontend in scope; backend API only.
- Entity map source: hand-written seed file.
- Re-enrichment: when the map changes, only markets enriched under an
  older map version are re-enriched.
- Entity aliases: not in v1.

## Acceptance Criteria

- Seed file with ~50 entities in each of the 6 categories loads into
  the `entities` table.
- Enrichment worker picks up unenriched markets from all three sources,
  runs the two-pass Jev flow, and writes `market_entities` rows and
  `market_enrichment` status; failures are recorded and retryable.
- Bumping the seed file's `map_version` causes only markets enriched
  under the older version to be re-enriched.
- `GET /entities/autocomplete?q=Tes` returns Tesla.
- `GET /entities/{id}/markets` returns every enriched market linked to
  that entity across Kalshi, Polymarket, and Polymarket US.
