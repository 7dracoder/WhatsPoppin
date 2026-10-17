# Polymarket (global) → Postgres worker

Streams live quotes and trades for the most-traded open Polymarket markets
under `POLYMARKET_TAGS` into the same tables as Kalshi (`source = 'polymarket'`).
The alert detector picks them up unchanged. No API keys are needed: global
Polymarket market data is public.

- `polymarket.py`: Gamma REST discovery, order-book tracking, WebSocket session, row builders
- `__main__.py`: entry point. Discovery plus the session; the rest is `ingestion/common/`

## Run

```
uv run python -m backend.ingestion.polymarket
```

When it's working, the log shows:

```
... INFO ingestion.polymarket: Following 500 markets across tags politics, economy, finance, tech, geopolitics
... INFO ingestion.polymarket.polymarket: WebSocket connected
... INFO ingestion.polymarket: Rows written in the last minute: 676 prices, 32 trades (queued: 2)
```

| Variable | Meaning |
|---|---|
| `POLYMARKET_TAGS` | Gamma tag slugs to follow, e.g. `politics,economy,finance,tech,geopolitics` |
| `POLYMARKET_MAX_MARKETS` | Follow the top N open markets by 24h volume (default 500) |

## How it maps to our tables

- **"YES" = outcome 0.** Every row is stored from the point of view of
  `clobTokenIds[0]` / `outcomes[0]`. For Yes/No markets that's "Yes". For
  others (team or candidate names), `markets.outcome_label` says what YES means.
- **Market fields:**

  | Column | Comes from |
  |---|---|
  | `market_id` | `conditionId` |
  | `title` | the `question` |
  | `outcome_label` | `groupItemTitle` (else `outcomes[0]`) |
  | `event_id` / `event_title` | the Gamma event |
  | `series_id` | `seriesSlug` |
  | `category` | the first configured tag the event has |
  | `url` | `https://polymarket.com/event/<event slug>` |

- **Quotes.** A `market_prices` row is written on every `best_bid_ask` for
  token 0, plus one snapshot row from the first `book` after (re)subscribing.
  - Sizes at the best bid/ask come from a level map kept in memory from
    `book` and `price_change` messages.
  - `volume` and `open_interest` are null; the stream doesn't carry them.
- **Trades.** One `market_trades` row per `last_trade_price`, converted to the
  YES view:

  | Trade | `yes_price` | `taker_side` |
  |---|---|---|
  | token 0, BUY | price | `yes` |
  | token 0, SELL | price | `no` |
  | token 1, BUY | 1 − price | `no` |
  | token 1, SELL | 1 − price | `yes` |

- **Discovery.** Runs at startup, on reconnect and every 5 min. Up to 5 pages
  of 100 events per tag, most-traded first, deduped by market. It sends
  subscribe/unsubscribe for the difference. Markets that drop out are set to
  `status = 'closed'`, as are markets that send `market_resolved`.

## Verified live (2026-10-10)

1. **`last_trade_price.side` is the taker's side on that token.** Of 265 BUY
   trades, 246 printed at the best ask; 51 of 55 SELLs printed at the bid.
2. **Each trade appears once,** on the token the taker traded (320 trades, 320
   unique transaction hashes, none on both tokens). So the worker subscribes
   to both tokens and converts token-1 trades as in the table above.
3. **`size` is in shares** (outcome tokens paying $1), fractions allowed
   (e.g. 367.666667 at $0.06). Notional = size × price, the same as Kalshi.
4. **Subscription limits:**
   - One subscribe message with ~1,000 token IDs silently fails (books came
     back for only 4–12 tokens).
   - Chunks of 200 per message work, all within a second. The worker sends
     200 per message on one connection.
   - About half of long-tail tokens have no book at all, so they never send a
     snapshot.
5. **`custom_feature_enabled` must be on every subscribe message.** One message
   without it turns `best_bid_ask` off for the whole connection.
6. **Volume and keepalive:**
   - `price_change` is heavy: ~50/s for 1,000 tokens in these tags, and ~640/s
     for the 120 hottest tokens.
   - Under that flood, the `websockets` library's protocol pings timed out once
     ("no close frame"). The worker therefore turns them off and relies on
     Polymarket's own text `PING` every 10 s. That run then stayed connected for
     6+ minutes.
7. **`book` messages repeat** after trades, not only on subscribe. Only the
   first one after a subscribe is marked `snapshot`.

## Tests

`uv run pytest tests/ingestion/polymarket`
