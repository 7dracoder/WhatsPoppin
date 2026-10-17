# What'sPoppin — Specs

**Goal:** map out the differences between prediction markets (Kalshi,
Polymarket) and actual stock movement — where prediction-market sentiment
and the real market agree, diverge, or lead/lag each other.

Focus areas:

- **Prediction markets as a financial signal:** detect and explain market
  moves, connect markets to the companies they affect, test lead/lag
  against stock prices. Data: Polymarket, Kalshi public API, SEC EDGAR.
- **AI for finance:** flag companies worth watching, gauge sentiment from
  public discourse and news, with evaluation against real data.

## Specs

| Spec | Status |
|---|---|
| [ingestion/](./ingestion/README.md) — News Aggregator, Twitter Aggregator, Polymarket, Kalshi, Scheduler | Draft |
| [ingestion/company-graph-tasks.md](./ingestion/company-graph-tasks.md) — company network graph | Built |
| [jev-classifier.md](./jev-classifier.md) | Draft |
| [twitter-jev-classification.md](./twitter-jev-classification.md) | Draft |
| [trending-cards.md](./trending-cards.md) | Draft |
| [display-charting.md](./display-charting.md) | Draft |
| [market-search.md](./market-search.md) — entity enrichment + search API | Built (backend) |
| [market-price-history.md](./market-price-history.md) — market + stock chart data | Built (backend) |
| [top-markets-helper.md](./top-markets-helper.md) — top 3 markets for a company | Built (backend) |
| [alert-company-impact.md](./alert-company-impact.md) — alerts matched backwards to affected companies | Built (backend) |
| [news-graphing.md](./news-graphing.md) | Draft |
| [fastapi.md](./fastapi.md) | Draft — scaffolding only |
| [frontend-design-spec.md](./frontend-design-spec.md) / [frontend-architecture-spec.md](./frontend-architecture-spec.md) | Built |

## Rough Data Flow

```
ingestion/scheduler ──► triggers all 4 sources below on their intervals

ingestion/news-aggregator    ─┐
ingestion/twitter-aggregator ─┼──► jev-classifier ──┬──► trending-cards
                               │                      ├──► news-graphing ──► (plotted on) display-charting
                               │                      └──► company-network (co-mention signal)
ingestion/polymarket         ─┐
ingestion/kalshi             ─┴──► display-charting (prediction-market overlay)
                                     trending-cards (market context)

company-network ───────────────────► trending-cards / news-graphing (entity context)
```

## Spec Template

Every spec (except this index and the ingestion overview) follows the same
section order: Problem/Why, Goals, Non-Goals, User Stories, Functional
Requirements, Design/Approach, Interfaces/Data Model, Dependencies, Open
Questions, Acceptance Criteria.
