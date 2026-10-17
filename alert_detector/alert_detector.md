# Alert detector

Watches the live data that the ingestion workers (Kalshi, Polymarket, Polymarket US)
write to Postgres. One detector covers every source: markets are keyed by
`(source, market_id)`, and grouping and cooldowns never cross sources.
When a market move looks real, it writes a row to `alerts`. A separate LLM
enricher (not built yet) picks up each alert and researches it using tweets,
news and the graph DB.

- `signals.py`: the maths, as pure functions (no I/O, `now` passed in)
- `state.py`: per-market rolling buffers of good quotes, trades and reconnect gaps
- `detector.py`: exclusions, escalation, grouping by event, cooldown, summary, context
- `db.py`: initial load, incremental polling, alert insert and notify
- `config.py`: thresholds, overridable in `.env`
- `models/alert.py` (repo root): the `alerts` table, created on startup

## Run

The ingestion workers must be running, because the detector reads their tables.
The baseline job (`python -m baselines`, see `baselines/baselines.md`) must have run
at least once: without a baseline, a market's signals say "no baseline yet".
From the repo root:

```
uv run python -m alert_detector            # runs until Ctrl+C
uv run python -m alert_detector --explain  # one evaluation, prints why, writes nothing
uv run python -m alert_detector --explain --source polymarket  # same, one source only
```

How it runs:

- **Startup.** Loads the last 20 minutes of prices and trades into memory,
  plus every market's baseline from `market_baselines`.
- **Every second (`POLL_S`).** Reads the new rows (`id > last seen`) and
  re-evaluates every market that got one.
- **Every minute.** Reloads market metadata and baselines, drops old data, and logs one line:
  evaluations, candidates and alerts written.

## The four signals, in plain words

The window is the last 5 minutes. All "normal" levels come from `market_baselines`,
computed from days of history by the baseline job and refreshed every 8 hours.

1. **Price move.** How far the midpoint moved in 5 minutes, divided by how far
   it usually moves in 5 minutes. That gives a z-score; it fires at |z| ≥ 3.
   - Only good quotes count: both sides have orders and the spread is ≤ 10 pts.
   - Skipped when a reconnect gap falls in the window, when the price is pinned
     near 0 or 1, or when the baseline has fewer than 30 five-minute samples.
   - Typical movement has a floor of 1 point, so a tiny wiggle in a dead market
     doesn't look huge.
2. **Volume burst.** Dollars traded in the window compared with the normal
   dollars per 5 minutes. Fires at 5× normal and at least $300.
   - Dollars, not contracts: 10,000 contracts at $0.01 is only $100.
3. **Whale.** Any single order bigger than this market's 99th-percentile order
   size, from the baseline (at least $500). It uses a flat $1,000 when the
   baseline has fewer than 50 orders.
   - Several fills with the same timestamp and side count as one order.
   - Block trades are flagged.
4. **Imbalance.** What share of the window's taker dollars bought the same
   side; 0 = balanced, 1 = all one side.
   - Needs at least $300 and 5 orders.
   - Never alerts on its own: it only counts next to a price move or a volume burst.

A market becomes an alert candidate if:

- the price move fires, or
- a whale fires, or
- a volume burst fires together with imbalance ≥ 0.6.

Other rules:

- **Events.** Strikes of one event move together, so the strongest market in
  the event becomes the alert. The others are listed in `context.related_markets`.
- **Cooldown.** An event can't alert again for 10 minutes, unless the new move
  scores at least 1.5× the last one.
- **Skipped markets.** Markets closing within 15 minutes are skipped.
- **Ingestion down.** If no new data arrives for 120 s, nothing is alerted.

## Output contract (`alerts`)

| Column | Meaning |
|---|---|
| `status` | `pending` → `processing` → `done` / `failed` (the enricher updates it) |
| `source` | `kalshi`, `polymarket` or `polymarket_us` |
| `market_id`, `event_id`, `series_id` | which market (the strongest in its event) |
| `direction` | `yes_up` or `yes_down` |
| `reasons` | which signals fired: `price_move`, `volume_burst`, `whale`, `imbalance` |
| `score` | ranking. Combines z (capped at 3), volume ratio ÷ 5 (capped at 3), +1 for a whale, + imbalance |
| `window_start`, `window_end` | the 5-minute window |
| `mid_before`, `mid_now`, `change_pts`, `z_score`, `sigma` | the price move (0–1 scale) |
| `window_notional`, `volume_ratio` | $ traded in the window, and × normal |
| `imbalance`, `imbalance_side` | 0–1 one-sidedness, and toward which side |
| `whale_notional`, `whale_side`, `is_block_trade` | the whale order, if one fired |
| `summary` | one plain sentence for the LLM, starting with the platform, e.g. `[Polymarket] ...` |
| `context` (jsonb) | `market` metadata (incl. `source` and `url`), `price_path` (minute mids, last 30 min), `top_trades` (5 largest orders), `related_markets`, `thresholds` |

### How the enricher claims an alert

```sql
UPDATE alerts SET status = 'processing', claimed_at = now()
WHERE id = (SELECT id FROM alerts WHERE status = 'pending'
            ORDER BY score DESC, id LIMIT 1 FOR UPDATE SKIP LOCKED)
RETURNING *;
```

`FOR UPDATE SKIP LOCKED` lets several enricher workers run without grabbing
the same alert. Every new alert also sends `pg_notify('alerts', <id>)`, so an
enricher can `LISTEN alerts;` instead of polling.

## Demo: fake alerts on demand

Real moves are rare, so `demo.py` inserts fake markets whose data trips each
kind of alert. It then reports whether the running detector caught them.

```
uv run python -m alert_detector            # terminal 1, default thresholds
uv run python -m alert_detector.demo       # terminal 2, takes ~90 s
uv run python -m alert_detector.demo --source polymarket   # same, as Polymarket markets
uv run python -m alert_detector.demo --cleanup   # delete all demo data afterwards
```

| Scenario | Fake data | Expected |
|---|---|---|
| PRICE | Midpoint jumps 0.40 → 0.48 where 5-min moves are usually 1 pt | `price_move` |
| WHALE | One $2,000 order where orders are usually $40 | `whale` |
| BURST | $820 in 5 min (usually ~$10), 98% YES-buying | `volume_burst`, `imbalance` |
| ALL | Price jump + burst + $1,500 order + one-sided buying | all four |
| CHURN | One-sided buying, no price or volume change | no alert |
| CLOSING | Price jump in a market closing in 10 min | no alert |

The demo markets use the `KXDEMO` series. The ingestion workers never follow or
close markets in it, so the fake markets stay active until `--cleanup`.
The script waits 65 s after creating them because the detector reloads market
metadata once a minute. Run only one detector at a time, or each instance
writes its own copy of every alert.

## Config

Every threshold can be set in `.env` (see the "Alert detector" section of
`.env.example`). Real moves may not happen during a demo, so lower them, e.g.
`Z_MIN=1.5 BURST_RATIO=2`, and restart. `WINDOW_MIN` + `LOOKBACK_MIN` must fit
inside the ingestion's 30-minute retention.

## Verify

Open a SQL prompt with
`docker exec -it whatspoppin-postgres psql -U postgres -d whatspoppin`.

1. With ingestion running, `uv run python -m alert_detector` runs 10+ minutes
   without `ERROR` lines and logs a stats line every minute.
2. `uv run python -m alert_detector --explain` prints signal values per market.
   Skip reasons such as `warm-up 12/30 samples` are normal for the first hour.
3. Set `Z_MIN=1.5` and `BURST_RATIO=2` in `.env` and restart. Then:
   - `SELECT id, score, reasons, summary FROM alerts ORDER BY id DESC LIMIT 5;` shows rows.
   - The claim query above returns one row and sets it to `processing`.
4. `uv run pytest tests/alert_detector` passes.
