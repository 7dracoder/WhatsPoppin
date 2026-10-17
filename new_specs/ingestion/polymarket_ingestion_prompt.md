# Task: add Polymarket (global + US) as live sources and normalize the pipeline

## Context

What'sPoppin: a real-time feed showing which industries and companies are
affected by prediction-market moves. Already built and working end to end:

- `ingestion/kalshi/`: WebSocket worker that writes Kalshi `markets`,
  `market_prices` (one row per ticker update) and `market_trades` (one row per
  trade) to local Postgres. Run it with `python -m ingestion.kalshi`.
- `models/`: SQLAlchemy models. Tables are created from them with
  `create_all`; there are no migrations.
- `alert_detector/`: reads new rows, computes four signals per market, and
  writes `alerts`. It includes `demo.py`, which inserts fake scenarios.
- Read `ingestion/kalshi/kalshi_ingestion.md` and `alert_detector/alert_detector.md`
  first, and match their structure, style and logging.

Goal: add **Polymarket global** and **Polymarket US** as two more sources. Their
data is normalized into the same tables, so the alert detector and the future
LLM enricher work on all sources unchanged. Keep it bare bones and demoable.
Don't add anything not listed here.

Decisions already made:

- **Sources.** Values are `kalshi`, `polymarket` (global) and `polymarket_us`.
- **Market selection (global).** By tag slugs.
- **Schema.** Rename the Kalshi-specific columns to generic names.
- **Detector.** One detector handles all sources.

Work in phases, and run the tests plus a live check after each phase.
Show a short plan first, then build without waiting. **Stop and ask** if a
"verify" step contradicts this prompt.

## Stack (unchanged)

- Python 3.11+, asyncio
- `websockets`, `httpx`, SQLAlchemy async + `asyncpg`, `cryptography`, `python-dotenv`
- stdlib `logging` at INFO; no `print()` outside demo/explain output
- Decimal for parsing prices and sizes
- Dependencies go in the root `pyproject.toml`

---

## Phase 0: normalize the schema and share ingestion code

### Column renames

In `markets` and `alerts`:

| Old | New |
|---|---|
| `event_ticker` | `event_id` |
| `series` | `series_id` |
| `yes_sub_title` | `outcome_label` |

Update every reader and writer:

- the Kalshi worker
- the detector, including the `context` JSON keys and `summary`
- `demo.py`
- tests
- both `.md` docs and the SQL in them

### Indexes

`market_ids` from different sources must never be confused, so everything
keys on `(source, market_id)`:

- `market_prices` index → `(source, market_id, timestamp)`
- `market_trades` index → `(source, market_id, timestamp)`

### Recreating the tables

There are no migrations, so the local tables must be dropped and recreated.

- **Ask the user first.** `alerts` may hold rows they want to keep.
- Prices and trades only keep 3 hours anyway, and markets are rediscovered.
- Add `python -m ingestion.reset_db` (asks for "yes"). It drops `alerts`,
  `market_trades`, `market_prices` and `markets`, then runs `create_all`.

### Shared code

Move the source-independent parts of `ingestion/kalshi/db.py` to
`ingestion/common/db.py`:

- `connect`
- `RowWriter`
- retention
- `upsert_markets(engine, source, rows)`
- `close_missing(engine, source, group_ids, open_ids)`

They currently hardcode `'kalshi'`; take `source` as a parameter instead.
Also move the shared backoff/reconnect loop, stats log and Ctrl+C shutdown
into `ingestion/common/runner.py`. Each source worker then only provides:

- a discover function
- a session class with `run()`, which calls `on_price(row)` / `on_trade(row)`

Kalshi must behave exactly as before after the refactor. Check that its live
run and its tests still pass before continuing.

---

## Phase 1: Polymarket global (`ingestion/polymarket/`, `python -m ingestion.polymarket`)

### Verified facts (checked live on 2026-10-10)

**Discovery: REST, Gamma API.** No auth, base `https://gamma-api.polymarket.com`.

- `GET /events?tag_slug=<slug>&active=true&closed=false&limit=100&offset=N`
  lists open events with their markets nested.
  - `limit` is capped at 100; paginate with `offset` until a page is short.
  - The same event can appear under several tags, so dedupe by event `id`.
  - Open markets per tag: politics 1,297; finance 703; business 760;
    economy 552; tech 439; crypto 431; geopolitics 351. That's too many to
    follow all of them, so cap the total.
- **Event fields:** `id`, `slug`, `title`, `description`, `endDate`,
  `seriesSlug`, `negRisk`, `volume24hr`, `markets[]`, and `tags[]` (each with
  `id`, `label`, `slug`).
- **Market fields:**
  - `id`, `conditionId` (hex), `question`, `slug`, `description` (the rules text)
  - `groupItemTitle` (e.g. "Map 1 Winner", or the strike/candidate name inside an event)
  - `endDate` (ISO 8601), `active`, `closed`, `acceptingOrders`, `enableOrderBook`
  - `negRisk`, `orderPriceMinTickSize` (0.01 or 0.001)
  - `bestBid`, `bestAsk`, `lastTradePrice` (floats), `volume24hr`
- **Two fields are JSON strings, not arrays.** Parse them with `json.loads`:
  - `outcomes`, e.g. `"[\"Yes\", \"No\"]"`. It can also hold team or candidate
    names, e.g. `"[\"Aurora Gaming\", \"Vitality\"]"`.
  - `clobTokenIds`: two token IDs, as long decimal strings. `clobTokenIds[i]`
    is the token for `outcomes[i]`.

**Stream: WebSocket.** `wss://ws-subscriptions-clob.polymarket.com/ws/market`.
No auth.

- **Heartbeat.** Send the text frame `PING` every 10 s. The server replies
  `PONG` (plain text, not JSON), which you ignore.
- **Subscribe:**
  `{"assets_ids": [<token ids>], "type": "market", "custom_feature_enabled": true}`
  - `custom_feature_enabled` turns on `best_bid_ask`, `new_market` and
    `market_resolved`.
- **Add or remove tokens without reconnecting:**
  `{"assets_ids": [...], "operation": "subscribe"}` or `"operation": "unsubscribe"`.
- **Message framing.** A frame can be a JSON object or a JSON array of
  objects. Each object has `event_type`, and `timestamp` is a string of Unix
  milliseconds.
  - Right after subscribing, one `book` per token arrives as an array.
- **Message types:**
  - `book`: `market` (conditionId), `asset_id`, `bids`/`asks` arrays of
    `{price, size}` strings, `hash`. Full depth.
  - `price_change`: `market`, plus `price_changes[]` where each entry has
    `asset_id`, `price`, `size` (the new total at that level; "0" = removed),
    `side` (`BUY` = bid level, `SELL` = ask level), `best_bid`, `best_ask`.
    **Very high volume:** about 56 messages/s for just 12 tokens.
  - `best_bid_ask`: `market`, `asset_id`, `best_bid`, `best_ask`, `spread`.
    Sent only when the top of the book changes, about 0.35/s per token.
  - `last_trade_price`: `market`, `asset_id`, `price`, `size`, `side`,
    `fee_rate_bps`, `transaction_hash`.
  - `tick_size_change`, `new_market`, `market_resolved`
    (with `winning_asset_id`, `winning_outcome`).

### Verify during the build (log what you find in the README)

1. **What `last_trade_price.side` means.**
   - Hypothesis: it's the taker's side on that asset (`BUY` = the taker
     bought this outcome).
   - Check: `BUY` trades should print at or near the best ask, `SELL` trades
     at or near the best bid.
2. **Whether trades show up on both tokens.** Does a trade appear once, or on
   both tokens of a market (e.g. same `transaction_hash`)? This decides the
   subscription scheme below.
3. **Units of `size`.** Most likely shares (outcome tokens that pay $1 each),
   which would make notional = size × price, the same as Kalshi.
4. **Subscription limits.** Is there a maximum number of `assets_ids` per
   connection or per message? If subscribing about 1,000 tokens fails, use
   several connections of N tokens each.

### Normalization: Polymarket → our tables

| Our column | Polymarket global | (Kalshi, for comparison) |
|---|---|---|
| `source` | `polymarket` | `kalshi` |
| `market_id` | `conditionId` (the WS `market` field) | ticker |
| `title` | market `question` | market title |
| `outcome_label` | `groupItemTitle`, else `outcomes[0]` | yes_sub_title |
| `rules_primary` | market `description` | rules_primary |
| `event_id` / `event_title` | event `id` / `title` | event_ticker / event title |
| `series_id` / `series_title` | event `seriesSlug` / null | series ticker / title |
| `category` | label of the first configured tag the event has | event category |
| `tags` | event tag labels | series tags |
| `close_time` | market `endDate` | close_time |
| `status` | `active`; `closed` when it drops out of discovery or `market_resolved` arrives | same |

**"YES" = outcome 0.** All prices and trades are stored from the point of
view of `clobTokenIds[0]` / `outcomes[0]`. For Yes/No markets that's "Yes".
For others, `outcome_label` says what YES means.

- **Quotes.** Keep a per-token level map (`price → size` for bids and asks)
  from `book` and `price_change`. This is cheap dict updates; don't store it.
- **When to write a `market_prices` row.** On each `best_bid_ask` (and once
  per `book` snapshot) for token 0, **not** on every `price_change`:
  - `yes_bid` / `yes_ask` come from the message.
  - `yes_bid_size` / `yes_ask_size` come from the level map at those prices.
  - An empty side means that side's size is 0. Never invent a price.
- **`price_or_odds` (NOT NULL).** The last `last_trade_price` for token 0.
  Seed it from Gamma's `lastTradePrice`; if neither exists, use the mid.
- **`volume`, `open_interest`.** Null; the stream doesn't provide them.
- **`snapshot`.** True on the row written from a `book` message received
  right after a subscribe or reconnect (the same idea as Kalshi).
- **Trades (`market_trades`):**
  - `trade_id` = `transaction_hash`, plus `:` and the asset_id if needed for
    uniqueness.
  - `count` = `size`.
  - `is_block_trade` = false.
  - Convert to the YES point of view:

    | Trade | `yes_price` | `taker_side` |
    |---|---|---|
    | token 0, BUY | price | `yes` |
    | token 0, SELL | price | `no` |
    | token 1, BUY | 1 − price | `no` |
    | token 1, SELL | 1 − price | `yes` |

  - If verify step 2 shows a trade appears on both tokens, **subscribe to
    token 0 only**. Otherwise subscribe both tokens and use the table above;
    write quote rows from token 0 only.
- **Followed set.** Markets in the configured tags where all of these hold:
  - `acceptingOrders` and `enableOrderBook` are true
  - `clobTokenIds` has 2 entries
  - `endDate` is in the future

  Keep the top `POLYMARKET_MAX_MARKETS` by market `volume24hr`.
- **Discovery schedule.** At startup, on every reconnect, then every 5
  minutes. Send `subscribe` / `unsubscribe` for the difference.
- **Resolution.** On `market_resolved`, set the market's status to `closed`
  and unsubscribe its tokens.

### Config

```
POLYMARKET_TAGS=politics,economy,finance,tech,geopolitics
POLYMARKET_MAX_MARKETS=500
```

Add both to `.env.example` with comments.

### Tests: `tests/ingestion/polymarket/test_parse.py`

- `outcomes` / `clobTokenIds` given as JSON strings parse correctly
- `book` → level map → correct top sizes
- `price_change` with size "0" removes a level
- `best_bid_ask` with an empty side → size 0, and the detector's good-quote
  rule rejects it
- trade on token 1 converts to the YES view (price 1 − p, flipped side)
- an array frame and an object frame both parse
- `PONG` is ignored
- a malformed message is skipped, never a crash
- the first book after a subscribe is marked `snapshot`

---

## Phase 2: one alert detector for all sources

- **Key everything by `(source, market_id)`:**
  - `State.markets`, `Detector.markets`, the baseline cache
  - `poll_new` returns `(source, market_id)` pairs; `db.py` selects `source`
  - `load_markets` returns all sources
  - cooldown keys become `f"{source}:{event_id or market_id}"`
  - grouping by event stays within one source
- **`alerts.source`** is set from the market.
- **`summary`** gets the platform name in front, e.g.
  "[Polymarket] Fed decision in December (25 bps cut): YES rose ...".
- **`context.market`** adds `source` and a `url`:
  - Kalshi: `https://kalshi.com/markets/<series_id>`
  - Polymarket: `https://polymarket.com/event/<event slug>`

  The enricher and UI will want the link. Store the event slug in `markets`
  as a new nullable `url` column, filled by each source's discovery.
- **Thresholds stay global.** All money is USD (USDC on Polymarket), and the
  per-market baselines already absorb the size differences between platforms.
  Polymarket's 0.001 tick and frequent prices near 0.999 are covered by the
  existing spread and pinned-price rules.
- **`demo.py`** gets a `--source kalshi|polymarket` flag (default kalshi) and
  writes demo markets under that source. Test both.
- **Tests:**
  - extend `tests/alert_detector/test_signals.py` so that two sources with the
    same `market_id` and event never mix (state, grouping, cooldown)
  - update the existing tests for the renamed columns

---

## Phase 3: Polymarket US (`ingestion/polymarket_us/`, `python -m ingestion.polymarket_us`)

**These facts are NOT verified.** They come from unofficial SDKs.

**Step 1: read the official US docs.** docs.polymarket.com links them as "US
Docs"; keys are created at polymarket.us/developer. Confirm or correct every
line below. If market data can't be streamed with the user's keys, stop and
report.

**What the unofficial SDKs say:**

- **Hosts:**
  - Public REST market data: `https://gateway.polymarket.us`
  - Trading and streams: `https://api.polymarket.us`
- **Market stream:** `wss://api.polymarket.us/v1/ws/markets`.
  - It requires auth even for market data.
  - Auth is three `X-PM-*` headers on the upgrade request.
  - The signature is Ed25519 over `timestamp_ms + "GET" + path` (path without
    query), base64-encoded. That's the same pattern as Kalshi, so reuse
    `ingestion/kalshi/kalshi.py:sign` (it already handles Ed25519 keys).
- **Subscription types:**
  - `SUBSCRIPTION_TYPE_MARKET_DATA` (full book)
  - `SUBSCRIPTION_TYPE_MARKET_DATA_LITE` (best bid/offer). Prefer this for quotes.
  - `SUBSCRIPTION_TYPE_TRADE` (trades)
- **Market IDs:** markets are identified by slug, e.g. `btc-100k-2025`.
- **REST client methods:** ListMarkets, FetchMarket, FetchEvent, FetchBook,
  FetchBBO. Find their paths in the docs.

**Credentials (in `.env`):**

- `POLYMARKET_US_KEY_ID`, `POLYMARKET_US_SECRET_KEY`
- Rename the user's tentative `POLYMARKET_KEY_ID` / `POLYMARKET_SECRET_KEY`
  to these, the names the SDKs use.
- Global Polymarket needs no keys.

**Build:**

- Same structure, normalization and tests as phase 1, with
  `source = polymarket_us`.
- Selection: `POLYMARKET_US_MAX_MARKETS` (default 200). Use a category filter
  if the API has one; otherwise take the top markets by volume.
- Prices may be quoted in cents or in dollars; normalize to 0–1 dollars.

**Known limitation (don't solve):** many US markets mirror global ones. The
same real-world move can alert twice, once per source; that's acceptable for
the demo.

---

## Out of scope

- Trading or order placement
- Private/user channels
- Cross-source market matching or dedupe
- Historical backfill beyond what the detector loads
- Redis, Docker for workers, CI, migrations tooling, metrics

## Definition of done

Run from the repo root:

1. `python -m ingestion.kalshi`, `python -m ingestion.polymarket` and
   `python -m ingestion.polymarket_us` each run 10+ minutes without errors,
   with a `Rows written` line every minute.
2. `SELECT source, count(*) FROM markets WHERE status='active' GROUP BY 1;`
   shows all three sources.
3. `SELECT source, count(*) FROM market_prices GROUP BY 1;` grows for every
   source. `market_trades` also grows, more slowly.
4. `python -m alert_detector --explain` lists markets from more than one source.
5. `python -m alert_detector.demo --source polymarket` passes every scenario
   while the detector runs.
6. `pytest tests/` passes.
7. Verify steps 1–4 of phase 1, and every phase 3 fact, are written up in the
   Polymarket README files.
8. Tell the user exactly what to run to check each point.

---

## Appendix: how the platforms differ (for the README and the demo pitch)

| | Kalshi | Polymarket global | Polymarket US |
|---|---|---|---|
| Regulation | US CFTC exchange | Offshore, crypto (USDC on Polygon) | US CFTC exchange (separate from global) |
| Market data auth | Keys required for the WebSocket | None | Keys required (per SDKs) |
| Market id | Ticker, e.g. `KXBTCD-26OCT1017-T82999.99` | `conditionId` hex + 2 token IDs | Slug |
| YES / NO | One book; NO is implied (1 − YES) | Two tokens with mirrored books; outcomes can be names | One book per market (verify) |
| Price tick | $0.01 | $0.01 or $0.001 | verify |
| Quote updates | `ticker`: any field change, with sizes | `best_bid_ask` (top only) + `price_change` (every level) | BBO "lite" stream |
| Trades | `trade`: count, taker outcome side | `last_trade_price`: size, side on that token | trade stream |
| Grouping | series → event → markets (strikes) | event → markets (`groupItemTitle`); negRisk = mutually exclusive outcomes | events → markets |
| Typical topics | Weather, crypto strikes, economics, politics | Politics, geopolitics, crypto, tech, sports | Sports-heavy (verify) |
| Volume / OI in stream | yes (`volume_fp`, `open_interest_fp`) | no | verify |

**Insight angle for the demo.**

- When the same question trades on Kalshi and Polymarket, their prices can
  disagree. A move that shows up on one platform first is a strong signal.
- Polymarket's politics, tech and business tags map most directly to
  "which companies are affected".
