# Ingestion: SEC Filings

**Status:** Draft

See [ingestion overview](./README.md) for why this is a third ingestion
shape — filing text — distinct from content items and market time-series.

## Problem / Why

A company's 10-K is legally required to disclose material business risks
in Item 1A ("Risk Factors"), and companies routinely name real external
exposures close to verbatim — an oil/energy firm citing Middle East
shipping lanes, a hardware firm citing Taiwan semiconductor supply, an
importer citing tariffs. That text is a real, structured-enough signal for
[[market-relevance]] to match against tracked prediction markets, but
nothing in this project currently fetches or stores it.

## Goals

- Fetch a company's most recent 10-K from SEC EDGAR for a small, tracked
  set of demo companies.
- Extract the Item 1A ("Risk Factors") section as plain text and store it.
- Make that text available to [[market-relevance]] for exposure
  extraction and market matching.

## Non-Goals

- Extracting exposures/themes from the filing text, or matching them to
  markets — that's [[market-relevance]]'s job; this spec only fetches and
  stores clean section text.
- `10-Q` filings — only `10-K` is in scope. 10-Qs often omit or only
  partially update Item 1A, which adds parsing-boundary inconsistency for
  no clear benefit at this scope.
- Filing sections other than Item 1A (e.g. Item 1 "Business", MD&A,
  financial statements) — out of scope for v1; Item 1A's language maps
  most directly to "exposure."
- Arbitrary ticker → CIK lookup — v1 works off a hardcoded small demo
  company list (5-10 tickers chosen for having clean, exposure-rich
  filings), not a general lookup feature.
- Retry/backoff logic — same posture as [[ingestion/news-aggregator]];
  EDGAR's rate limit is a non-issue at single-digit-company, on-demand
  scale.

## User Stories / Example Interactions

- As the system, when a tracked company's latest 10-K is fetched, I want
  its Risk Factors text stored so [[market-relevance]] can extract
  exposures from it.
- As a developer testing locally, I want to manually trigger a fetch for
  one demo company instead of waiting on a schedule.

## Functional Requirements

1. For each company in the hardcoded demo list, resolve its SEC EDGAR CIK
   (hardcoded alongside the ticker for v1 — no lookup service).
2. Fetch the company's most recent `10-K` filing via the EDGAR API,
   sending the required descriptive `User-Agent` header (see Design /
   Approach).
3. Extract the Item 1A ("Risk Factors") section as plain text, using
   EDGAR's section-tagged data where available, falling back to a regex
   heuristic on `Item 1A.` / `Item 1B.` header text otherwise.
4. On extraction failure (nonstandard filing formatting), log and skip
   that filing rather than failing the whole run.
5. Store each fetched filing as a row (see Interfaces / Data Model),
   de-duplicated on EDGAR's accession number so re-fetching the same
   filing is a no-op.
6. Support both an on-demand/manual trigger (single company) and a
   scheduled check for new filings (see Design / Approach).

## Design / Approach

Unlike near-real-time news/Twitter polling, filings are quarterly/annual —
this source should behave like [[ingestion/polymarket]]'s fetch-on-demand
posture, not a tight polling loop. Two trigger paths:

- **On-demand**, for a single demo company — used for local testing and
  the demo itself.
- **Scheduled**, at a long interval (daily or weekly), checking EDGAR's
  submissions feed for any new 10-K filed for a tracked company since the
  last check. This still fits [[ingestion/scheduler]] unchanged — the
  scheduler already treats interval length as each source's own config
  and already requires on-demand trigger support; this source just picks
  "long interval + on-demand" as its values.

EDGAR requires a descriptive `User-Agent` header (`"<app name>
<contact email>"`) per its fair-use policy. The contact email isn't a
secret, but per [tech-stack.md](../../architecture/tech-stack.md)'s `.env`
convention it should be config-driven, not a hardcoded literal.

## Interfaces / Data Model

Draft shape (not final) — one row per fetched filing:

```
{
  "entity_symbol": "<ticker, FK to entities.symbol>",
  "cik": "<EDGAR company identifier>",
  "accession_number": "<EDGAR filing id, de-dup key>",
  "filing_type": "10-K",
  "fiscal_period_end": "<date>",
  "filed_at": "<ISO 8601>",
  "item_1a_text": "<extracted Risk Factors section, raw text>",
  "source_url": "<link back to EDGAR>"
}
```

See [db design](../../mock_db_design/db-design.md) for the corresponding
`filings` table.

## Dependencies

- SEC EDGAR API (no API key required; descriptive `User-Agent` header
  required; fair-use rate limit, ~10 req/sec).
- [[ingestion/scheduler]] — triggers the scheduled check path.
- [[market-relevance]] — downstream consumer of stored filing text.

## Open Questions

- Which section-tagged EDGAR endpoint (if any) reliably returns Item 1A
  boundaries, vs. needing the regex-header fallback for most filers?
- Final demo company list (5-10 tickers) — not picked yet.
- How often should the scheduled check actually run — daily vs. weekly —
  given filings are quarterly/annual at most?

## Acceptance Criteria

- A manually-triggered fetch for one demo company's ticker returns its
  most recent 10-K's Item 1A text, stored and de-duplicated, within a
  demo-able time window.