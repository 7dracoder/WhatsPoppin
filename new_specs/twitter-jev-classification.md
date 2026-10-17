# Twitter × Jev Classification

**Status:** Draft

See [[jev-classifier]] for the general classifier design and
`backend/classification/` for what's actually built (`classify()`,
`is_relevant()`/`filter_relevant()`, the five modes). See
[[ingestion/twitter-lookup]] for where Twitter `ContentItem`s come from,
and [[fastapi]] for the API layer this returns through. Both ends already
exist; this spec is the glue between them, which doesn't: a tweet comes
in, Jev classifies it, the result goes straight back out through the API
— nothing persisted in between.

## Problem / Why

`backend/classification` already works and is source-agnostic by design
— `classify(title, text, spec)` takes plain strings, not a source's
`ContentItem` class, specifically so it doesn't need to know about news
vs. Twitter (see `classifier.py`'s own docstring). But "plain strings" is
exactly where the gap is: it assumes a primary `title` field and an
optional `text` body, which fits news (headline + article body) but not
tweets, which have no headline at all —
[[ingestion/twitter-lookup]]'s `ContentItem.title` is always `None`.
Calling `classify()` naively with a tweet's fields in the news order
produces a broken `state` string. This spec nails down the correct
calling convention, decides which mode actually answers this project's
real question for a tweet, and wires the whole thing (lookup → classify)
behind one API call that returns the result directly — no storage.

## Goals

- Feed a Twitter `ContentItem` into `backend/classification` without
  corrupting the `state` sent to Jev.
- Classify every single post an account/keyword lookup returns against
  one entity, in one Jev call per post — relevance and stance decided
  together, not as a separate filter-then-classify pass.
- Return only the posts that actually correlate to the entity — anything
  Jev decides is unrelated is dropped from the response, not just marked.
- One request in (account or keyword lookup + entity), one response out
  — synchronous, nothing written to storage.

## Non-Goals

- Persisting classification results anywhere — this is request-in,
  response-out. If a future feature needs classified tweets on file,
  that's a separate spec, not this one.
- Changing `backend/classification` itself — it's shared with news and
  works correctly for news today; any fix belongs in how Twitter content
  is *adapted before* calling it, not in the classifier.
- Feeding engagement metrics (likes/reposts/replies/quotes) into the Jev
  prompt — that's a separate, already-numeric signal, returned alongside
  the classification result rather than fed into it. See Design/Approach.
- Entity/ticker extraction — [[ingestion/twitter-lookup]]'s
  `EntityMatcher` already tags candidate entities via substring match on
  ingestion, but this endpoint doesn't gate on it: a post with no keyword
  hit can still mention an entity's business indirectly (e.g. a tariff
  policy post that never says "Nvidia"), so every post gets its own Jev
  call regardless of whether the cheap match fired.
- A hard cap on how many posts get classified per request — Jev is cheap
  and fast enough that classifying everything a lookup returns (up to
  [[ingestion/twitter-lookup]]'s own ~3,200-tweet ceiling) isn't a cost
  or latency concern worth designing around.
- Building any entity-tagging system (tagging companies, tagging
  Kalshi/Polymarket markets for cross-correlation, e.g. a "strait of
  hormuz" market tagged `oil` matching an oil company's own `oil` tag)
  — that's a market↔company correlation problem, [[company-network]]'s
  territory, not this spec's, and not something this endpoint looks up
  or stores. `tags` here is just an optional value the caller passes in
  on the request (Functional Requirement 6, tier 1) — where that value
  comes from, or whether a curation system exists at all, is entirely
  out of scope.

## User Stories / Example Interactions

- As a user, I want to request as much of an account's history as
  [[ingestion/twitter-lookup]] can return, have every single post
  checked against an entity, and get back only the ones that actually
  correlate — bullish or bearish, not just "mentions it."
- As the system, a post that never says "NVDA" but still bears on it
  (e.g. a chip-tariff policy post) should still be caught, since
  relevance is decided by Jev reading the post, not by a keyword match.
- As [[display-charting]]/[[news-graphing]], I want a tweet's
  classification result in the response payload already, in the same
  shape a news item's would be.

## Functional Requirements

1. **Input adapter**: before calling `classify()` on a Twitter
   `ContentItem`, pass its `text` as the `title` argument and `None` as
   the `text` argument — not `(item.title, item.text)` in that order.
   `item.title` is always `None` for a tweet; calling
   `classify(None, item.text, spec)` hits the `if text` branch in
   `classifier.py` and produces the literal state string
   `"None\n\n<tweet text>"`, which is wrong. Calling
   `classify(item.text, None, spec)` instead produces exactly the tweet
   text as `state`, correctly.
2. Fetch as much of the account's/keyword's history as
   [[ingestion/twitter-lookup]] will return (its own ~3,200-tweet cap
   for an account lookup) — don't default to a narrow lookback window
   the way [[fastapi]]'s existing `/twitter/account` endpoint does.
3. Classify every single post returned, one Jev call each, with a single
   `ChoiceSpec` carrying four labels: `{bullish, bearish, neutral,
   unrelated}`. One call decides both correlation and stance at once.
   This replaces the separate `is_relevant()` pre-filter step a news
   item would go through — not needed here since the choice itself
   already encodes relevance as its fourth option.
4. Run these calls concurrently (`ThreadPoolExecutor`, same pattern
   `relevance.py`'s `filter_relevant()` already uses for independent Jev
   calls) rather than sequentially — cheap/fast per call, but hundreds to
   thousands of posts still adds up if run one at a time.
5. Drop a post only if `unrelated`'s probability clears a tunable
   threshold (default 0.6, not hardcoded — same pattern `is_relevant()`'s
   own `threshold` param already uses), not just whichever label has the
   highest raw probability. Keep the rest, each with its full
   `ClassificationResult` (including the full `probabilities` dict)
   attached. See Design/Approach for why argmax alone isn't trusted here.
6. **Indirect-relevance category source, tiered**: the question's
   "indirect relevance" categories (tariffs, competitors, supply chain,
   regulation, ...) come from the first of these that's available, in
   order — generated once per entity per request, not once per tweet,
   same reasoning as before (the categories depend on the entity, not
   the individual post):
   1. **Caller-supplied tags**: an optional `tags` query param on the
      request itself (e.g. `tags=oil,shipping`) — not looked up from
      anywhere, not stored anywhere, just whatever the caller passes in
      this one request. If present, used as-is.
   2. **Dynamically generated via Haiku**, if the caller didn't supply
      `tags` but an LLM is reachable: one call to
      `backend/llm.complete_structured()` (already built, defaults to
      `anthropic/claude-3.5-haiku` — "cheap/fast; structured extraction,
      not reasoning," per that module's own docstring) with a small
      response model (e.g. `IndirectRelevanceCategories(categories:
      list[str])`) produces 3-6 short phrases for the given entity.
      "Reachable" means: `OPENROUTER` is set and the call doesn't raise
      `LlmError` — checked once per entity per request, not assumed.
   3. **A generic static default**, if neither of the above is
      available (no caller-supplied `tags`, no reachable LLM) — one fixed list broad
      enough to apply to almost any public company: regulation or policy
      changes, major competitors, supply-chain or trade partners,
      macroeconomic conditions, trade restrictions or tariffs. This is
      the floor every entity gets, never a hard failure.

   Whichever tier wins, the resulting phrases get interpolated into the
   `ChoiceSpec` question reused across every concurrent `classify()`
   call in the batch.
7. Expose this as one endpoint, one entity per call —
   `GET /twitter/classify?handle=...|query=...&entity=NVDA&tags=oil,shipping`
   (`tags` optional) — extending [[fastapi]]'s `/twitter` router.
   Computed per request, not read back from anywhere.
8. Provide a benchmark/test script, `scripts/bench_jev_twitter.py`,
   mirroring `scripts/bench_jev_news.py`'s pattern: fetch a real
   account's tweets via [[ingestion/twitter-lookup]], classify every one
   against an entity, and write each post's full result (text, label,
   full `probabilities` dict, confidence) to a JSON file — default
   `twitter_jev_test.json` — for manual review. This is the actual
   mechanism for checking the "does everything just get marked
   unrelated" concern (see Design/Approach) rather than assuming the
   label set and wording work; same role `bench_jev_news.py` played in
   tuning `is_relevant()`'s wording for news (see that script's
   docstring and `relevance.py`'s). No automated accuracy scoring —
   there's no ground truth, so this is eyeball review of the printed/
   saved label distribution and per-post probabilities, same caveat
   `bench_jev_news.py` documents about itself.

## Design / Approach

Request-scoped pipeline, run per request, nothing persisted:

```
GET /twitter/classify?handle=realDonaldTrump&entity=NVDA&tags=oil,shipping
  -> lookup_account(..., as wide a range as twitter-lookup allows)
       [[ingestion/twitter-lookup]]
  -> resolve indirect-relevance categories once (tags param -> Haiku -> generic default)
  -> concurrently, for every ContentItem returned (ThreadPoolExecutor):
       adapt: (title=item.text, text=None)
       classify(adapted.title, adapted.text, ChoiceSpec(
         question="Does this post relate to NVDA, directly or via: oil, shipping?",
         labels={"bullish": ..., "bearish": ..., "neutral": ..., "unrelated": ...}))
  -> drop a result only if probabilities["unrelated"] >= threshold (default 0.6)
  -> return [{...tweet fields..., "classification": {...}}]
```

Engagement metrics stay out of the Jev call entirely — they're already
on `ContentItem.engagement` and returned as-is in the same response,
not passed to Jev. Whatever's on the other end of the API call (a chart,
an alert correlation) can weight the classification by engagement itself
without this layer doing it.

**Guarding against `unrelated` becoming the lazy default.** A 4-label
choice risks the model defaulting to the "safe" label whenever an entity
isn't named literally — `relevance.py`'s own docstring documents hitting
exactly this for news (an earlier wording scored everything ~0.5, a coin
flip, no separation between real signal and junk) before the question
was reworded to explicitly name what counts as indirect relevance.
Carried over here:

- The question names indirect relevance explicitly (tariffs, export
  controls, competitors, supply chain, regulation), not just "does this
  mention NVDA" — same fix that worked for news.
- The drop decision reads the full `probabilities` dict against a
  tunable threshold, not raw argmax — same pattern `is_relevant()`
  already uses (`threshold: float = 0.5`, not hardcoded). A post where
  `unrelated` barely edges out `bullish` (0.42 vs 0.35) is a different
  situation than `unrelated` winning at 0.9, and a flat argmax can't
  tell them apart.
- `scripts/bench_jev_twitter.py` (Functional Requirement 7) is how this
  gets checked empirically against real tweets before anyone trusts the
  endpoint, rather than assumed from the wording alone.

## Interfaces / Data Model

No new persisted shape — this is a request/response pair, not a stored
record. Response is a list of the existing `ContentItem` fields plus the
existing `ClassificationResult`, combined:

```
[
  {
    "source": "twitter", "author": "realDonaldTrump",
    "text": "...", "entities": ["NVDA"],
    "engagement": {"likes": 128620, "reposts": 21890, "replies": 12438, "quotes": 2088},
    "url": "...", "published_at": "...",
    "classification": {
      "mode": "choice", "label": "bullish", "probability": 0.78,
      "probabilities": {"bullish": 0.78, "bearish": 0.06, "neutral": 0.10, "unrelated": 0.06},
      "confidence": 0.81, "raw": {...}
    }
  }
]
```

Only posts whose winning label isn't `unrelated` appear in the response
at all — `entities` may be empty even on a returned post, since this
endpoint doesn't rely on the keyword match to decide inclusion.

## Dependencies

- `backend/classification` (`classify`, `ChoiceSpec`) — already built,
  unmodified by this spec.
- `backend/llm` (`complete_structured`, default
  `anthropic/claude-3.5-haiku`) — already built, used for tier 2 of the
  category-source fallback (Functional Requirement 6).
- [[ingestion/twitter-lookup]] — source of the `ContentItem`s and their
  pre-tagged `entities`.
- [[fastapi]] — the `/twitter` router this extends with a `/classify`
  endpoint.

## Open Questions

None open — see Resolved below.

### Resolved

- Title/text adapter (Functional Requirement 1) lives inline in the
  `/twitter/classify` route itself — nothing else needs it, so no
  separate shared module.
- Entity tags (Functional Requirement 6, tier 1) are a plain optional
  `tags` query param on the request — not looked up, not stored, not
  tied to any curation system. Where tags might eventually come from
  (a company-tagging system, [[company-network]]) is explicitly out of
  scope here, per Non-Goals.
- One entity per request/API call — `entity` stays a single value, not
  a list.

## Acceptance Criteria

- A manufactured tweet with no title field, run through the adapter and
  `classify()`, produces a sensible `state` string (not a literal
  `"None\n\n..."` prefix) and a correct label.
- `GET /twitter/classify?handle=...&entity=NVDA` against a manufactured
  bullish tweet and a manufactured bearish tweet about NVDA returns the
  correct label for each, directly in the response — no intermediate
  storage involved.
- A manufactured tweet clearly unrelated to NVDA (`unrelated`'s
  probability clears the threshold) is absent from the response
  entirely, not returned with an `unrelated` label.
- Running `scripts/bench_jev_twitter.py` against a real account's
  tweets produces `twitter_jev_test.json` with every classified post's
  label and full `probabilities` dict, reviewable by hand.
