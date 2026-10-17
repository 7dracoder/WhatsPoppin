# Polymarket US → Postgres worker

Streams live quotes and trades from Polymarket US into the same tables as
Kalshi (`source = 'polymarket_us'`). Polymarket US is the CFTC-regulated US
exchange, separate from global Polymarket. Its market stream requires API keys.

- `polymarket_us.py`: Ed25519 signing, gateway REST discovery, WebSocket session, row builders
- `__main__.py`: entry point. Discovery plus the session; the rest is `ingestion/common/`

## Setup and run

1. Create a key at [polymarket.us/developer](https://polymarket.us/developer)
   (needs a verified Polymarket US account). The secret is shown once.
2. Add to `.env`:

   | Variable | Meaning |
   |---|---|
   | `POLYMARKET_US_KEY_ID` | Key ID |
   | `POLYMARKET_US_SECRET_KEY` | Secret key, base64 as given |
   | `POLYMARKET_US_TAGS` | Tag slugs, default `politics,tech,crypto` |
   | `POLYMARKET_US_MAX_MARKETS` | Default 200 |

3. Run:

   ```
   uv run python -m backend.ingestion.polymarket_us
   ```

   The log should show `Following 200 markets across tags politics, tech, crypto`
   and a `Rows written` line every minute.

## How it maps to our tables

- **One instrument per market.** Buying it = YES, selling it = NO, and its
  price is the YES price, so no conversion is needed.
- **Market fields:**

  | Column | Comes from |
  |---|---|
  | `market_id` | the market slug |
  | `title` | the `question` (e.g. "U.S Senate Midterm Winner") |
  | `outcome_label` | the market `title` (e.g. "Democratic Party") |
  | `event_id` / `event_title` | the event |
  | `series_id` | `seriesSlug` |
  | `url` | `https://polymarket.us/event/<event slug>` |

- **Quotes.** `SUBSCRIPTION_TYPE_MARKET_DATA` sends the top book levels plus
  stats on every change.
  - A `market_prices` row is written only when the top of the book (price or
    size) changes, or as the snapshot row after a market is first subscribed.
  - `volume` = `stats.sharesTraded`, `open_interest` = `stats.openInterest`.
  - The timestamp is `transactTime`.
- **Trades.** `SUBSCRIPTION_TYPE_TRADE`:
  - `taker.side` BUY → `taker_side = 'yes'`; SELL → `'no'`
  - `count` = `quantity.value` (contracts)
  - `trade_id` = the trade `id`
- **Discovery.** Uses `GET /v2/tags/<slug>/events` on `https://gateway.polymarket.us`
  (public, sorted by volume). It runs every 5 min; on any change the worker
  unsubscribes everything and resubscribes in chunks of 100.

## Verified against the official docs (docs.polymarket.us) and live (2026-10-10)

- **Auth headers.** The handshake needs `X-PM-Access-Key`, `X-PM-Timestamp`
  (ms) and `X-PM-Signature`: base64 Ed25519 over `timestamp + "GET" + "/v1/ws/markets"`.
  - The secret is base64; its first 32 bytes are the Ed25519 seed (not a PEM file).
  - The signing reuses `ingestion/kalshi/kalshi.py:sign`.
  - Timestamps must be within 30 s of server time.
- **Stream.** `wss://api.polymarket.us/v1/ws/markets`. It needs auth even for
  market data. REST market data on `gateway.polymarket.us` is public.
- **Subscribe format:**
  `{"subscribe": {"requestId", "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA" | "..._LITE" | "..._TRADE", "marketSlugs": [...]}}`
  - At most 100 markets per subscription.
  - Unsubscribe with `{"unsubscribe": {"requestId"}}`.
  - Responses are camelCase, even though the overview page shows snake_case.
- **Message shapes.** Prices come as `{"value": "0.5920", "currency": "USD"}`.
  Book levels are `{"px": {...}, "qty": "13095.0000"}`.
  - `marketData` keys: `marketSlug`, `bids`, `offers`, `state`, `stats`,
    `transactTime`.
  - Timestamps have nanoseconds.
- **Taker side.** In 46 live trades, every taker BUY printed at the ask and
  every SELL at the bid. A SELL can carry intent `BUY_SHORT`, i.e. buying NO.
- **Rate limit.** REST allows 25 requests/s per IP. Discovery uses ~3–15
  requests every 5 min.
- **What it covers.** 200 markets give ~700–1,100 quote rows/min, but trades
  are rare (a few per minute).
  - Topics lean to sports, elections and crypto up/down markets.
  - The `economy` and `finance` tags are empty.

## Known limitation

Many US markets mirror global Polymarket ones. The same real-world move can
alert twice, once per source; that's acceptable for the demo.

## Tests

`uv run pytest tests/ingestion/polymarket_us`
