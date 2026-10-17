# Kalshi → Postgres worker

Streams live Kalshi ticker updates (price, bid/ask, sizes, volume) and every
executed trade (size, price, taker side) for the most-traded markets in
`KALSHI_CATEGORIES` (and `KALSHI_SERIES`) into Postgres. Every open market in them is
stored in `markets`, so search and enrichment see all of them. Live rows are kept for 30 minutes. Before they are deleted, they're
rolled up into hourly summaries in `market_hourly` (kept 14 days). Each market's
"normal" lives in `market_baselines`, computed by `python -m baselines`.

- `kalshi.py`: request signing, REST market discovery, WebSocket ticker + trade stream
- `__main__.py`: entry point. Discovery plus the session; everything else is shared
- `ingestion/common/`: shared by every source. `db.py` (table setup, market upserts,
  batched writer, retention) and `runner.py` (reconnect loop, stats log, Ctrl+C)
- `models/` (repo root): SQLAlchemy models `markets`, `market_prices`, `market_trades` and `alerts`.
  Every row has `source = 'kalshi'`; Polymarket workers write the same tables.
  To recreate the tables after a model change, run `python -m backend.ingestion.reset_db`.
  The tables are created from these models on startup; there are no migrations.

## Setup

1. Start Postgres. The local Docker container is `whatspoppin-postgres`,
   database `whatspoppin`:
   ```
   docker start whatspoppin-postgres
   ```
2. Copy `.env.example` to `.env` and fill it in:

   | Variable | Meaning |
   |---|---|
   | `DATABASE_URL` | Postgres URL, e.g. `postgresql://postgres:<pw>@localhost:5432/whatspoppin` |
   | `KALSHI_API_KEY_ID` | Kalshi API key ID |
   | `KALSHI_PRIVATE_KEY_PATH` | PEM file path (default `kalshi_key.pem`) |
   | `KALSHI_CATEGORIES` | Kalshi categories to follow, e.g. `Economics,Financials,Companies` |
   | `KALSHI_MAX_MARKETS` | How many markets get live prices: the top N by 24h volume (default 1000) |
   | `KALSHI_SERIES` | Extra series to follow, e.g. `KXHIGHNY,KXBTCD`. Alone, only these are followed |

3. Install dependencies: `uv sync`

## Run

From the repo root:

```
uv run python -m backend.ingestion.kalshi
```

When it's working, the log shows these lines:

```
... INFO ingestion.kalshi: Stored 19272 open markets across Economics, ...; streaming the top 1000 by 24h volume
... INFO ingestion.kalshi.kalshi: WebSocket connected
... INFO ingestion.kalshi: Rows written in the last minute: 454 prices, 79 trades (queued: 0)
```

Ctrl+C writes out whatever is still queued, then exits.

How it behaves:

- **Discovery.** Runs at startup, on every reconnect, then every 5 minutes.
  With `KALSHI_CATEGORIES`, it pages through every open Kalshi event (about 75
  pages, ~30 s, paced to stay under the rate limit) and keeps the categories'
  events plus any listed series. Every open market is upserted into `markets`;
  any Kalshi market no longer listed is closed. The top `KALSHI_MAX_MARKETS` by
  24h volume that haven't reached their close time get the WebSocket stream.
  With only `KALSHI_SERIES`, it asks for each series separately, as before.
  A 429 (rate limited) is retried with backoff.
- **Reconnects.** Uses exponential backoff: 1 s, doubling up to 30 s, plus a
  little random jitter.
- **Writing.** Rows are inserted in batches every 0.5 s. If an insert fails,
  the rows are retried on the next flush. Each queue (prices, trades) holds at most
  100k rows; beyond that the oldest are dropped.
- **Snapshots.** The first ticker after a subscribe has `snapshot = true`.
  It's Kalshi's current state for that market, and its timestamp can be hours old.
- **Trades.** One `market_trades` row per executed trade. `taker_side` is the
  outcome the taker bought (`yes`/`no`); `count` is contracts, not dollars.

## Verify

Open a SQL prompt with
`docker exec -it whatspoppin-postgres psql -U postgres -d whatspoppin`.

1. Leave it running for 10+ minutes. The log should show no `ERROR` lines and a
   `Rows written` line every minute.
2. `SELECT count(*) FROM markets WHERE status = 'active';` returns more than 0.
3. `SELECT count(*) FROM market_prices;` grows when you run it again a few seconds later.
   `SELECT count(*) FROM market_trades;` grows too, more slowly (only when trades happen).
4. After 5+ minutes, the detector query below returns rows.
5. `uv run pytest tests/ingestion/kalshi` passes.

## Querying the data

The real alerting lives in `alert_detector/` (see `alert_detector/alert_detector.md`).
For a quick manual look, this query gives the 5-minute midpoint change over a
10-minute window. It covers every source and uses only trustworthy quotes:

```sql
WITH q AS (
  SELECT source, market_id, timestamp, (yes_bid + yes_ask) / 2 AS mid
  FROM market_prices
  WHERE timestamp > now() - interval '10 minutes'
    AND yes_bid_size > 0 AND yes_ask_size > 0       -- both sides have real orders
    AND yes_ask - yes_bid <= 0.10                   -- spread narrow enough to trust
), latest AS (
  SELECT DISTINCT ON (source, market_id) source, market_id, mid
  FROM q ORDER BY source, market_id, timestamp DESC
), ago AS (
  SELECT DISTINCT ON (source, market_id) source, market_id, mid FROM q
  WHERE timestamp <= now() - interval '5 minutes' ORDER BY source, market_id, timestamp DESC
)
SELECT m.source, m.market_id, m.title, m.event_title, m.category,
       ago.mid AS mid_5m_ago, latest.mid AS mid_now, latest.mid - ago.mid AS change
FROM latest JOIN ago USING (source, market_id) JOIN markets m USING (source, market_id)
WHERE m.status = 'active' AND m.close_time > now() + interval '15 minutes'
ORDER BY abs(latest.mid - ago.mid) DESC;
```

- `change` is in probability points: 0.05 = 5 points.
- Prices run from 0 to 1 and equal the implied chance of YES.
- `price_or_odds` is the last trade and can be stale, so use the midpoint instead.
- Markets near close naturally converge to 0 or 1, so the query skips them.
- Many strikes of one event move together, so group alerts by `(source, event_id)`.

For the classifier:

```sql
SELECT source, market_id, title, outcome_label, rules_primary, event_title, series_title,
       category, tags, url
FROM markets WHERE status = 'active';
```
