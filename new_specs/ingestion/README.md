# Ingestion — Overview

The ingestion layer has two different shapes in this design, not one shared
schema for everything:

1. **Content aggregators** — [News Aggregator](./news-aggregator.md) and
   [Twitter Aggregator](./twitter-aggregator.md). Each polls an external API
   on a heavily-configurable filter set and emits discrete items (articles,
   posts) in a shared normalized shape, so [[jev-classifier]] and the rest
   of the pipeline don't need source-specific handling.
2. **Prediction-market fetchers** — [Polymarket](./polymarket.md) and
   [Kalshi](./kalshi.md). Each lets the system pull market/price/odds data
   over time for a given market, used to overlay against stock price charts
   in [[display-charting]] — this is time-series market data, not
   classifiable text content, so it does not go through [[jev-classifier]].
3. **On-demand lookup** — [Twitter Lookup](./twitter-lookup.md) and
   [Stock Prices](./alpaca.md). Both fetch on demand rather than on a
   continuous schedule — Twitter Lookup triggered by a chart account
   lookup or a fired alert, Stock Prices triggered by a ticker/range
   selected on a chart.

| Spec | Source | Shape |
|---|---|---|
| [news-aggregator.md](./news-aggregator.md) | News API | Content item |
| [twitter-aggregator.md](./twitter-aggregator.md) | Twitter/X API | Content item |
| [twitter-lookup.md](./twitter-lookup.md) | Twitter/X API | Content item (on-demand: account or keyword) |
| [polymarket.md](./polymarket.md) | Polymarket public API | Market time-series |
| [kalshi.md](./kalshi.md) | Kalshi public API | Market time-series |
| [alpaca.md](./alpaca.md) | Alpaca Market Data API (free tier) | Price time-series (on-demand) |
| [scheduler.md](./scheduler.md) | — | Runs the four polled sources above on their configured intervals |

## Shared Normalized Content-Item Schema

Both content aggregators emit items in this shape (draft, not final):

```
{
  "source": "news" | "twitter",
  "id": "<source-native id>",
  "author": "<outlet | handle>",
  "title": "<headline, news only>",
  "text": "<raw content>",
  "entities": ["NVDA", ...],       // tickers/companies matched, if detected
  "url": "<source permalink>",
  "published_at": "<ISO 8601>",
  "engagement": {                  // twitter only, from tweet.fields=public_metrics
    "likes": 0, "reposts": 0, "replies": 0, "quotes": 0
  }
}
```

Entity matching is a direct lookup against `entities` (match on ticker,
company name, or person name — no fuzzy/NLP matching), done per-source by
each aggregator, not centrally in [[jev-classifier]]. This feeds
[[company-network]]'s co-mention signal and [[news-graphing]]'s chart
placement.

## Shared Dependencies

- Content-aggregator items flow to [[jev-classifier]].
- Prediction-market data flows to [[display-charting]] and
  [[trending-cards]].
- [scheduler.md](./scheduler.md) — triggers all four sources' polls; each
  source spec defines its own interval, the scheduler just runs them.

## Cross-Cutting Open Questions

- Storage layer: Postgres (see [db design](../../mock_db_design/db-design.md));
  exact stack (Python + which ORM, etc.) still being finalized.
- Default values for each aggregator's filter set — none are picked yet,
  see each spec's own Open Questions.
- Shared rate-limit/backoff strategy across workers, or fully independent
  per source? (See [scheduler.md](./scheduler.md)'s parallel-vs-sequential
  open question.)
