# Market Price History

**Status:** Built (backend): `backend/api/market_history.py`, mounted in `backend/main.py`

## Problem / Why

When a user clicks a market in the frontend, they should see a price chart for it, with
the standard stock-style time frame buttons, next to its related stock.

## Design (as built)

`GET /markets/{market_ticker}/history?stock={stock_ticker}&range={1D|1W|1M|6M|1Y|ALL}`

| Range | Window | Interval (Kalshi `period_interval`, Alpaca timeframe) |
|---|---|---|
| 1D | 1 day | 1 min (`1`, `1Min`) |
| 1W | 7 days | 1 hour (`60`, `1Hour`) |
| 1M | 30 days | 1 hour (`60`, `1Hour`) |
| 6M | 182 days | 1 day (`1440`, `1Day`) |
| 1Y | 365 days | 1 day (`1440`, `1Day`) |
| ALL | market open to now | 1 day (`1440`, `1Day`) |

- Live on each request; nothing is stored. Kalshi:
  `GET /series/{series}/markets/{ticker}/candlesticks` (max 5,000 candles per request, so
  every range fits in one call). Alpaca: the existing gateway's `fetch_bars` (IEX feed).
- The series ticker comes from the `markets` table (unknown market -> 404). The table has no
  open time, so it comes from Kalshi's `GET /markets/{ticker}` in the same request.
- `available_ranges`: every range shorter than the market's age, the first range that
  covers it, and ALL (a 2-day-old market: `1D, 1W, ALL`). Asking for another range -> 400.
- Market points are candle `price.close`; a candle with no close (no trade in it) is left
  out. Stock points are bar closes, stamped at the bar's end (Alpaca stamps the start) so
  `t` means the same in both series. No filling or interpolation; gaps stay gaps.
- The stock covers the whole window; the market only from its open time, so a young
  market's line starts partway through.

Response:

```json
{"market_ticker": "...", "stock_ticker": "TSLA", "range": "1W", "interval": "1h",
 "available_ranges": ["1D", "1W", "ALL"],
 "market": [{"t": 1760000000, "close": 0.42}], "stock": [{"t": 1760000000, "close": 251.30}]}
```

## Open Questions

- Thin markets: Kalshi has no close for periods without a trade, so 1D/1W charts of quiet
  markets can be empty (seen live: a GDP market had 0 points for 1D and 1W). Candles do
  carry the bid/ask close for every period; using its midpoint would fill these charts
  with real quotes.
- Kalshi rate limits under live-on-click load; revisit caching if it becomes a problem.
