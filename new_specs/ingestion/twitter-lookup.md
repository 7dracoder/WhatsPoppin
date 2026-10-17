# Ingestion: Twitter Lookup (On-Demand Account & Keyword Backfill)

**Status:** Draft

See [ingestion overview](./README.md). Sibling to
[twitter-aggregator.md](./twitter-aggregator.md), same X API, different
access pattern: on-demand backfill instead of continuous watchlist polling,
triggered either by a user looking up an account or by an alert firing on
a keyword/topic.

## Problem / Why

Two related gaps, both the wrong shape for
[twitter-aggregator.md](./twitter-aggregator.md)'s continuous
`twitter_watchlist` polling, which only ever sees things configured *in
advance*:

1. **User-driven account lookup.** [[news-graphing]] marks chart events
   sourced from [[jev-classifier]], which only sees items the watchlist
   was already polling for. That's the wrong access pattern for "did this
   specific person's tweets move this market" — the user names the
   account *at query time*, after picking a chart, not in advance.
2. **Alert-driven keyword search.** The Kalshi alert detector (see
   `alert_detector/`) can flag a market price spike *after the fact*. At
   that point we want to search for tweets matching that market's topic
   (e.g. "strait of hormuz", `#nvidia`) in the window around the spike —
   but the watchlist can't retroactively answer that unless that exact
   keyword happened to already be tracked before the spike occurred.

Continuously polling every account or keyword that might ever be looked up
would mean paying (X API billing is per post returned, not per request —
see twitter-aggregator.md's Open Questions) for things nobody has asked
about yet. This spec covers both triggers on demand instead: fetching only
the relevant account's or keyword's history for a bounded time window,
when something actually asks for it.

## Goals

- Let a user look up a specific X account (e.g. "Donald Trump") in the
  context of a chart they're already viewing in [[display-charting]].
- Let `alert_detector` trigger a keyword/topic search (derived from the
  market's `title`/`event_title`/`tags`) for the time window around a
  fired alert.
- Fetch the relevant tweet history for the requested time range, on
  demand, without requiring the account or keyword to be pre-added to
  `twitter_watchlist`.
- Emit fetched tweets in the same [shared content-item
  shape](./README.md#shared-normalized-content-item-schema) as
  twitter-aggregator.md, so [[news-graphing]] can mark them without
  source-specific handling.
- Capture each tweet's engagement (likes, reposts, replies, quotes) so a
  viewer can tell a viral post from a low-signal match, not just that it
  matched.

## Non-Goals

- Continuous/live tracking of a looked-up account/keyword, or promoting
  one to `twitter_watchlist` — out of scope for the MVP; every
  lookup is one-off, full stop.
- Replacing [twitter-aggregator.md](./twitter-aggregator.md)'s
  watchlist-driven pipeline — that pipeline still feeds
  [[trending-cards]] / [[company-network]] / general news-graphing markers
  independent of this feature.
- Classification itself — this spec only fetches and emits raw tweets as
  content items; [[jev-classifier]] does the actual classification pass
  downstream, same boundary as [twitter-aggregator.md](./twitter-aggregator.md).
- Computing a lead/lag statistic — same boundary [[display-charting]]
  already draws; this is still just visualization's data source.
- Full-archive search beyond what X's recent-search endpoint covers (see
  Open Questions) — if an alert fires on data older than that window,
  this feature simply can't backfill it.
- Deciding what counts as "hot"/significant from engagement counts — this
  spec only captures and emits the raw like/repost/reply/quote counts;
  any threshold or ranking is a downstream/UI judgment.

## User Stories / Example Interactions

- As a user viewing a stock/market overlay chart, I want to look up an
  account by handle and see markers for their tweets in the charted window,
  without anyone having pre-configured that account to be tracked.
- As a user, I want to spot-check "did this person's tweet move the
  market" for a one-off account I don't need tracked long-term.
- As the alert detector, when a market's price spikes, I want to search
  for tweets matching that market's topic in the window around the spike,
  even if nobody was tracking that keyword beforehand.

## Functional Requirements

1. Accept either (a) an account handle/display name, or (b) a keyword/
   topic query, plus a time range, as input.
2. For (a), fetch tweets via the X API user-timeline endpoint, scoped to
   that time range (not the watchlist/search path twitter-aggregator.md
   uses for continuous polling).
3. For (b), fetch tweets via the X API recent-search endpoint
   (`GET /2/tweets/search/recent`), using `start_time`/`end_time` set to
   the alert's window (e.g. spike time ± 15 minutes) — note this endpoint
   only covers roughly the last 7 days (see Open Questions).
4. De-duplicate against any tweets already stored for that account/query
   (e.g. from `twitter_watchlist`, if already tracked, or from a prior
   lookup of the same account/keyword/range).
5. Request `tweet.fields=public_metrics` on both endpoints so each fetched
   tweet carries its like/repost/reply/quote counts.
6. Store fetched tweets and emit them as normalized content items (see
   [ingestion overview](./README.md#shared-normalized-content-item-schema))
   with `"source": "twitter"` and the `engagement` field populated from
   `public_metrics`, same as twitter-aggregator.md's output.
7. Pass emitted items to [[jev-classifier]] for classification — this
   spec's job ends at raw tweets (+ relevant tags/event context pulled
   alongside them); classification happens downstream, not here.

## Design / Approach

Two triggers, same underlying lookup/store/emit logic, neither driven by
[scheduler.md](./scheduler.md) — this is on-demand, not polled:

- **Account lookup:** a UI action (selecting an account on a chart) calls
  the user-timeline endpoint, bounded by the chart's visible time range.
- **Alert-driven keyword search:** `alert_detector` calls a lookup function
  this spec exposes (e.g. `lookup_keyword(query, start, end)`) when it
  fires an alert, passing a keyword/topic derived from the market's
  `title`/`event_title`/`tags` (same columns `kalshi_ingestion.md`'s
  classifier query already selects) and a time window centered on the
  alert. `alert_detector` never calls the recent-search endpoint itself —
  same ownership boundary as `news_api/service.py`'s `poll_news()`: the
  rest of the codebase only calls into this module, never the X API
  directly.

Both are bounded, one-time calls rather than an ongoing poll, to keep each
lookup a small cost rather than a continuous one.

## Interfaces / Data Model

Uses the [shared content-item schema](./README.md#shared-normalized-content-item-schema)
with `"source": "twitter"`, identical to twitter-aggregator.md's shape.
`author` holds the tweet's account handle for both triggers (same as
twitter-aggregator.md). `entities` holds matched tickers/companies only —
same entity-matching convention as every other source, not the looked-up
account or raw keyword. `engagement` holds `public_metrics` as-is: likes,
reposts, replies, quotes — raw counts, no "hot" threshold applied here
(see Non-Goals: deciding what counts as significant is a downstream/UI
judgment, not this spec's job).

## Dependencies

- X API user-timeline endpoint (account lookup) and recent-search endpoint
  (keyword lookup) — both distinct from
  [twitter-aggregator.md](./twitter-aggregator.md)'s search/stream
  endpoints used for continuous polling.
- [[display-charting]] — triggers account lookups from its chart view.
- `alert_detector` — triggers keyword lookups when a price-move alert
  fires; see the query against `markets` (`title`, `event_title`,
  `category`, `tags`) in `kalshi_ingestion.md`'s "For the classifier"
  section for where the keyword comes from.
- [[news-graphing]] — renders the markers from the emitted content items.
- [[jev-classifier]] — classifies emitted content items downstream, same
  as [twitter-aggregator.md](./twitter-aggregator.md)'s items.
- [twitter-aggregator.md](./twitter-aggregator.md) — shares the
  content-item schema; no shared write path to `twitter_watchlist` (see
  Resolved below).

## Open Questions

None open — see Resolved below.

### Resolved

- No promotion to `twitter_watchlist`. Out of scope for the MVP —
  every lookup stays one-off, no path to ongoing tracking.

- Recent-search is keyword/topic-only, not account-scoped — confirmed;
  the ~7-day window is acceptable since alerts fire on recent data.
- User-timeline's ~3,200-tweet cap is fine for a demo.
- Repeated lookups of the same account/keyword/range can always re-fetch —
  no caching layer required, just the de-dup in Functional Requirement 4
  so storage doesn't get duplicate rows.
- Both triggers go through [[jev-classifier]] for classification; this
  spec only gets tweets (+ tags/events context) to it.
- The keyword/topic query combines exact phrase, hashtag, and cashtag via
  OR rather than picking one strategy, e.g. `"strait of hormuz" OR
  #hormuz OR $OIL` built from the market's `title`/`event_title`/`tags` —
  X's query syntax supports this natively (same boolean/OR building
  twitter-aggregator.md's FR #3 already assumes). Bounded by the query
  character limit, so a long `title` may need trimming to an exact phrase
  rather than being used whole; broader OR'ing also pulls in more matches,
  which costs more per lookup since billing is per tweet returned.

## Acceptance Criteria

- Looking up an account that was never in `twitter_watchlist`, for a chart
  showing a time range containing a manufactured demo tweet from that
  account, shows a marker at the correct timestamp within a demo-able time
  window.
- A manufactured alert on a market with a known topic (e.g. "strait of
  hormuz") returns a manufactured demo tweet matching that topic within
  the alert's time window, within a demo-able time window.
