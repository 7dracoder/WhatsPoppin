# Baseline job

Computes what "normal" looks like for every followed market, from days of
history, into the `market_baselines` table. The alert detector compares the
live 5-minute window against these numbers.

The detector used to measure "normal" from its own live buffer. That buffer
needed 1–2.5 hours of uninterrupted data after every restart, and until then it
fell back to thin defaults, which caused noisy whale alerts. Now each market's
baseline is ready as soon as the job has seen it, and live tables only keep 30
minutes.

- `sources.py`: fetches history per platform and converts it to the YES point of view
- `compute.py`: pure maths (typical 5-minute move, normal $ per 5 min, 99th-percentile order)
- `db.py`: which markets need a baseline, hourly rollups, upserts
- `__main__.py`: the loop
- `models/market_baseline.py`, `models/market_hourly.py` (repo root): the tables

## Run

From the repo root, next to the ingestion workers and the detector:

```
uv run python -m baselines            # keeps running
uv run python -m baselines --once     # one pass, then exit
uv run python -m baselines --force    # recompute every active market now
```

The first pass over ~1,000 markets takes about 6 minutes; it's rate-limited on
purpose. After that, the job checks every 5 minutes for:

- **new markets** (e.g. Kalshi's hourly Bitcoin strikes), which get a baseline
  within minutes, and
- **stale baselines** (older than 8 hours), which are recomputed.

A restart keeps the existing baselines; only missing or stale ones are fetched.

## What's stored per market

| Column | Meaning | Used for |
|---|---|---|
| `sigma_5m`, `sigma_samples` | standard deviation of 5-minute midpoint changes, and how many it used | price z-score (floor 1 pt; needs 30 samples) |
| `volume_per_window`, `history_minutes` | average $ traded per 5 minutes, over how much history | volume burst (needs 60 min) |
| `whale_p99`, `whale_orders` | 99th-percentile order size in $, and how many orders | whale threshold (floor $500; $1,000 if under 50 orders) |
| `computed_at`, `method` | when, and from which source | `--explain` shows the age |

## Where the history comes from

| Source | Prices | Trades (volume, whales) |
|---|---|---|
| Kalshi | 1-minute candlesticks, last 3 days (sparse: only minutes with activity) | `/markets/trades`, last 3 days |
| Polymarket | `clob /prices-history`, 5-minute points, last 3 days | `data-api /trades`, last 3 days |
| Polymarket US | gateway `/v1/price-history`, 5-minute bid/ask, last day | none public, so built from our own `market_hourly` rollups |

- **Volume** counts from the start of a market's history, so a market younger
  than 3 days doesn't look quiet because of days it didn't exist yet.
- **Busy markets:** trade history stops after 10 pages, and the period is
  counted from the oldest trade fetched.
- **Polymarket US** has no public trade history. The Report API needs an
  institutional account, and the RFQ trades endpoint only covers RFQ fills. So
  its volume and whale baselines come from `market_hourly`. The ingestion
  workers fill that table as they delete expired rows, and only hours we were
  actually watching count. Expect "thin history" for US markets during the
  first day.

## Results of the first run (2026-10-10)

| Source | Markets | Usable price baseline | Real whale p99 (≥ 50 orders) | Median whale p99 |
|---|---|---|---|---|
| Kalshi | 328 | 71 | 28 | $189 |
| Polymarket | 499 | 499 | 298 | $1,392 (top 10%: $5,500+) |
| Polymarket US | 200 | 184 | 0 (rollups start empty) | |

Most Kalshi markets are hourly or daily strikes only hours old, many without a
two-sided book, so few have 30 price samples yet. Polymarket's real whale bars
are well above the old $1,000 fallback. That fallback is what let this
morning's routine $1–4k NO orders on 1% markets alert.

## Tests

`uv run pytest tests/baselines`
