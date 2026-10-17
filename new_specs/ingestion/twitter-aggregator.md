# Ingestion: Twitter Aggregator

**Status:** Draft

See [ingestion overview](./README.md) for the shared content-item schema and
cross-source dependencies.

## Problem / Why

Twitter/X is the fastest-moving source of financial chatter — tickers,
cashtags, and specific accounts people follow for alpha. It feeds the same
downstream pipeline as [[news-aggregator]] ([[jev-classifier]],
[[trending-cards]], [[news-graphing]], [[company-network]]) but needs its
own filter set given how differently the platform's content and API are
shaped.

## Goals

- Poll the Twitter/X API on a configurable interval, applying the filter
  set below, and emit normalized content items.
- Support both account-based tracking (specific accounts) and
  topic-based tracking (hashtags, cashtags, keyword queries).

## Non-Goals

- Classifying/scoring posts — that's [[jev-classifier]].
- Posting/replying on behalf of a user — read-only ingestion only.

## User Stories / Example Interactions

- As a user, I want posts from accounts and cashtags I care about to feed
  trending cards and news graphing in near real time.
- As the system, when a post matches a tracked cashtag, I want it tagged so
  [[company-network]] and [[news-graphing]] can use it.

## Functional Requirements

1. Before each poll, read the active accounts/hashtags/cashtags/keywords
   from the shared `twitter_watchlist` table (see
   [db design](../../mock_db_design/db-design.md)) to build the query —
   this list is user-configurable, not hardcoded.
2. Poll the Twitter/X API on a configurable interval, respecting a
   configurable rate-limit budget.
3. Support filtering by: specific accounts, excluded accounts, hashtags,
   cashtags, keyword and phrase queries, boolean operators and exclusions,
   language, contains-links, time window, geography.
4. Support quality/relevance filters: minimum engagement, account
   verification or account age.
5. De-duplicate retweets and quote-tweets.
6. Emit each matched post as a normalized content item (see [ingestion
   overview](./README.md#shared-normalized-content-item-schema)).

## Design / Approach

This source exposes an async poll function that the shared
[scheduler](./scheduler.md) triggers on this source's configured interval —
running in-process alongside FastAPI, not as a separate worker (see
[tech-stack.md](../../architecture/tech-stack.md)). The scheduler only
triggers the poll; everything inside it (querying `twitter_watchlist`,
calling the X API, filtering, de-duplicating, emitting content items) stays
owned by this source.

Exact polling vs. streaming approach inside that poll function still
depends on which X API tier is available for the MVP — left light
until that's confirmed.

## Interfaces / Data Model

Uses the [shared content-item schema](./README.md#shared-normalized-content-item-schema)
with `"source": "twitter"`.

## Dependencies

- Twitter/X API (tier/access level TBD — affects streaming vs. polling).
- `twitter_watchlist` (db design) — source of the active accounts/hashtags/
  cashtags/keywords to poll for.
- [[jev-classifier]] — downstream consumer of emitted items.

## Open Questions

- Which X API tier/access do we have for the MVP, and does it
  support streaming or only polling/search?
- Defaults for the quality filters (minimum engagement, account age) — none
  picked yet.
- Who/what adds entries to `twitter_watchlist` — a UI, a config file, an
  API? Resolved so far only that it's a shared list, not per-user and not
  hardcoded.

## Acceptance Criteria

- A post matching a configured filter set appears as a normalized content
  item within a demo-able time window.
