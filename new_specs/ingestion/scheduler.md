# Ingestion Scheduler

**Status:** Draft

See [ingestion overview](./README.md) for the four sources this coordinates.

## Problem / Why

Every ingestion spec ([news-aggregator](./news-aggregator.md),
[twitter-aggregator](./twitter-aggregator.md), [polymarket](./polymarket.md),
[kalshi](./kalshi.md)) defines its own configurable poll interval, but none
of them say what actually triggers that poll. Without a single place that
runs all sources, each one would need to reinvent its own
cron/loop/scheduling logic independently.

## Goals

- Run each ingestion source on its own configurable interval, without each
  source managing its own scheduling.
- Support triggering one source, or all sources, on demand — useful for
  local testing/demo without waiting for the interval.
- Keep source-specific polling logic fully owned by each source; the
  scheduler only triggers, it doesn't know what a source does internally.

## Non-Goals

- Classification, storage, or entity-matching logic — that's each source's
  own job (and downstream specs like [[jev-classifier]]).
- Historical backfill — this is about ongoing/live polling. A source doing
  a one-off historical pull (e.g. [[polymarket]]'s stretch goal) is that
  source's own concern, not the scheduler's.

## User Stories / Example Interactions

- As a developer, I want all four ingestion sources polling automatically
  once the system is running, without manually running each one.
- As a developer testing locally, I want to manually trigger a single
  source's poll on demand instead of waiting for its interval.
- As the system, I want one source's interval to be independent of the
  others (e.g. Twitter polling every minute, markets every 15 minutes).

## Functional Requirements

1. Maintain a per-source schedule, using each source's own configured poll
   interval (see each ingestion spec's Functional Requirements).
2. Trigger each source's poll at its configured interval.
3. Support an on-demand/manual trigger, for a single source or for all
   sources at once.
4. Isolate failures — one source erroring out shouldn't block or crash the
   others' scheduled runs.
5. (Nice to have) Record each run — source, start time, finish time,
   success/failure, item count — for debugging/visibility.

## Design / Approach

Confirmed: an async, in-process scheduler running alongside FastAPI in the
same process — not a separate worker/service (see
[tech-stack.md](../../architecture/tech-stack.md)). It calls each source's
poll function directly on that source's configured interval; a distributed
task queue (Celery, etc.) is overkill for this scope.

## Interfaces / Data Model

No new storage table required to function — this is a runtime/orchestration
concern, not a data source. Optionally, run history could be logged to a
`scheduler_runs` table (source, started_at, finished_at, status,
item_count) if persisted debugging is wanted — not added to the db design
yet; flagged as an open question below instead.

## Dependencies

- [news-aggregator.md](./news-aggregator.md)
- [twitter-aggregator.md](./twitter-aggregator.md)
- [polymarket.md](./polymarket.md)
- [kalshi.md](./kalshi.md)

## Open Questions

- Actual poll interval per source — each spec says "configurable" but no
  defaults are picked yet.
- Should run history be persisted (a `scheduler_runs` table) or is
  console/log output enough for the MVP?
- Do sources run in parallel or sequentially? (Matters if sources share a
  rate-limited resource or the DB connection pool is small.)

## Acceptance Criteria

- All four ingestion sources run automatically on their own configured
  intervals without manual intervention.
- A single source can be triggered on demand, outside its normal schedule.
