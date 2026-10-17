# Jev Classifier

**Status:** Draft

## Problem / Why

Raw news/Twitter items aren't directly useful until something judges what
they mean for a given company or question. The Jev Classifier labels each
incoming content item against a set of questions — either simple
positive/negative sentiment, or questions the user defines — so downstream
features ([[trending-cards]], [[news-graphing]], [[company-network]]) work
with judged signal instead of raw text.

This is a narrower role than the old `specs/jev-classification.md`'s
six-use-case design (guardrail, routing, actionability gate, etc.) — none of
that is carried forward here. Only question-based classification is in
scope for this direction.

## Goals

- Classify each content item from [[ingestion/news-aggregator]] and
  [[ingestion/twitter-aggregator]] against a configurable set of questions.
- Support a simple default mode (positive/negative sentiment) for when no
  custom questions are defined.
- Support user-defined questions (e.g. "does this article suggest NVDA
  earnings will beat expectations?").

## Non-Goals

- Guardrail/dedup/routing logic (duplicate detection, block reasons,
  agent routing) — not part of this direction.
- Building/maintaining the company relationship graph — that's
  [[company-network]].

## User Stories / Example Interactions

- As a user, I want a quick positive/negative read on a news item without
  defining anything myself. (`sentiment` mode — zero config, fixed
  positive/negative/neutral labels.)
- As a user, I want to ask a custom yes/no question (e.g. "is this bullish
  for semiconductors?") and have every matching item classified against it.
  (`boolean` mode.)
- As the system, when an item is classified, I want that result available
  to [[trending-cards]] and [[news-graphing]] so they can surface it.

**`choice` mode** — sentiment's fixed positive/negative/neutral isn't
enough once the categories are domain-specific rather than "good vs bad."
`choice` is the same underlying Jev primitive as `sentiment` (one
question, one winning label from a set, a probability per label), except
the user supplies both the question and the label set instead of getting
them hardcoded. Concrete queries a user could configure:

- "What type of event does this describe?" → `{earnings, fed-decision,
  tariff-sanctions, product-launch, regulatory-action, other}` —
  consistent event-type tags across every item, for later backtesting.
- "Which of these companies is this article mainly about?" → `{NVDA, AMD,
  INTC, none-specifically}` — disambiguates a multi-chipmaker roundup from
  an article that's actually about one of them (the per-entity relevance
  problem [[entities]]/[[classification/relevance]] also addresses, as a
  yes/no instead of a pick-one).
- "What action, if any, does this news suggest for a position in NVDA?" →
  `{buy-signal, sell-signal, hold-no-action, needs-more-info}` — a
  discrete recommendation bucket, not a sentiment score.
- "Which downstream desk should review this?" → `{earnings-desk,
  policy-desk, supply-chain-desk, discard}` — the original spec's
  "support routing" use case, reframed as `choice`.
- "What is the overall analyst recommendation in this article?" →
  `{upgrade, downgrade, maintain, initiate-coverage, not-mentioned}`.

Implemented as `SentimentSpec`/`ChoiceSpec` in `backend/classification`
(see `modes.py`) — both wrap Jev's `Choice` primitive; `multi_select`
(also implemented) covers the case where more than one label can apply
at once, since Jev's `Choice` itself is single-select only.

## Functional Requirements

1. Accept a content item (from [[ingestion/news-aggregator]] or
   [[ingestion/twitter-aggregator]]) and a set of questions to classify it
   against.
2. Default to simple positive/negative sentiment when no custom questions
   are configured.
3. Support user-defined questions, each producing its own classification
   result per item.
4. Attach classification results to the item (or a linked record) so
   downstream features can read them without re-running classification.

## Design / Approach

Left light. Model choice unconfirmed — the project is still called "Jev"
per the source doc but which model/API backs it is undecided.

## Interfaces / Data Model

Draft shape (not final):

```
{
  "item_id": "<content item id>",
  "question": "<default: positive/negative | user-defined text>",
  "result": "<classification output — exact shape TBD, e.g. positive/negative, or a score>"
}
```

## Dependencies

- [[ingestion/news-aggregator]], [[ingestion/twitter-aggregator]], and
  [[ingestion/twitter-lookup]] — upstream content sources.
- [[trending-cards]] and [[news-graphing]] — consumers of classification
  results.

## Open Questions

- What model/API backs classification?
- Exact output shape per question — binary, score, free text?
- Can a user define questions at query time, or only ahead of time as
  configuration?
- Does this layer also do entity/ticker extraction, or is that fully owned
  by the aggregators (see [ingestion overview](./ingestion/README.md))?

## Acceptance Criteria

- A manufactured positive-sentiment item and a manufactured negative-sentiment
  item are classified correctly under the default mode.
- A manufactured item is correctly classified against at least one
  user-defined question.
