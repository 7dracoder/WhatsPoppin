"""`/markets/{ticker}/history` router: a Kalshi or Polymarket market's price chart with its related stock.

Spec: new_specs/market-price-history.md. Fetched live on each request (Kalshi candlesticks or
Polymarket price history, Alpaca bars); nothing is stored. Both series hold only real points: no fill, no interpolation.
Mounted in backend/main.py.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.api.deps import get_alpaca_gateway, get_db_engine
from backend.ingestion.alpaca import AlpacaApiError, AlpacaApiGateway
from backend.api.trading_days import parse_end, previous_day, snap_to_session, MAX_STEPS_BACK
from backend.ingestion.alpaca.normalize import normalize_batch
from backend.ingestion.kalshi.kalshi import REST_URL, fetch_candles, fetch_market
from backend.ingestion.polymarket import polymarket
from backend.models import Market

router = APIRouter(prefix="/markets", tags=["market-history"])

Range = Literal["1D", "1W", "1M", "6M", "1Y", "ALL"]
DAY = timedelta(days=1)
# range -> (window, Kalshi period_interval in minutes, Alpaca timeframe); ALL's window is the market's age.
RANGES: dict[str, tuple[timedelta | None, int, str]] = {
    "1D": (DAY, 1, "1Min"),
    "1W": (7 * DAY, 60, "1Hour"),
    "1M": (30 * DAY, 60, "1Hour"),
    "6M": (182 * DAY, 1440, "1Day"),
    "1Y": (365 * DAY, 1440, "1Day"),
    "ALL": (None, 1440, "1Day"),
}
INTERVAL_LABEL = {1: "1m", 60: "1h", 1440: "1d"}


class Point(BaseModel):
    t: int  # Unix seconds, end of the candle / bar
    close: float
    # Traded in the candle / bar: contracts (Kalshi) or shares (Alpaca). None when the venue has no
    # volume feed (Polymarket's price history), so the chart can tell "no volume" from "zero volume".
    volume: float | None = None


class HistoryOut(BaseModel):
    market_ticker: str
    stock_ticker: str
    range: str
    interval: str  # '1m' | '1h' | '1d'
    available_ranges: list[str]
    market: list[Point]
    stock: list[Point]
    window_end: datetime  # where the window actually ended: `end` as given, or the last session with bars
    open_time: datetime  # when the market opened on its venue: how young it is decides how it can be sampled


def available_ranges(open_time: datetime, now: datetime) -> list[str]:
    """Every range shorter than the market's age, the first one that covers it, and ALL.
    A 2-day-old market gets 1D, 1W and ALL.
    """
    age = now - open_time
    ranges = []
    for name, (window, _, _) in RANGES.items():
        if window is None:
            continue
        ranges.append(name)
        if window >= age:
            break
    return ranges + ["ALL"]


def window(range_: str, open_time: datetime, now: datetime) -> tuple[datetime, datetime]:
    """(start, end) of the chart. The stock covers the whole window; the market only has
    points from its open time on, so its line starts later when it is younger than the range.
    """
    span = RANGES[range_][0]
    return (open_time if span is None else now - span), now


def _volume(c: dict) -> float | None:
    """A Kalshi candle's traded contracts: `volume_fp` (a fixed-point string, "57.25") on the
    dollars API, `volume` (an int) on the older one. None when the candle carries neither.
    """
    raw = c.get("volume_fp")
    if raw in (None, ""):
        raw = c.get("volume")
    return None if raw in (None, "") else float(raw)


def market_points(candles: list[dict]) -> list[Point]:
    """Candle close prices. A candle without a close (nothing traded in it) is left out."""
    points = []
    for c in candles:
        close = (c.get("price") or {}).get("close_dollars")
        if close not in (None, ""):
            points.append(Point(t=int(c["end_period_ts"]), close=float(close), volume=_volume(c)))
    return sorted(points, key=lambda p: p.t)


def stock_points(bars: list[dict], ticker: str, interval_min: int) -> list[Point]:
    """Alpaca bar closes. Alpaca stamps a bar with its start; `t` is its end, like Kalshi's."""
    step = interval_min * 60
    return [Point(t=int(p.timestamp.timestamp()) + step, close=p.price_or_odds, volume=p.volume)
            for p in normalize_batch(ticker, bars)]


def _parse_time(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None


# Venues with a history feed below. Polymarket US ("polymarket_us") has none yet.
VENUES = ("kalshi", "polymarket")


async def _venue(engine: AsyncEngine, market_id: str) -> tuple[str, str | None] | None:
    """(source, series_id) of a stored market on a venue we can chart, or None. The id decides the
    venue: a Kalshi ticker and a Polymarket conditionId never look alike.
    """
    async with engine.connect() as conn:
        row = (await conn.execute(select(Market.source, Market.series_id).where(
            Market.source.in_(VENUES), Market.market_id == market_id))).first()
    return (row[0], row[1]) if row else None


class OutcomeOut(BaseModel):
    source: str
    market_id: str
    title: str | None
    outcome_label: str | None
    event_title: str | None
    rules_primary: str | None
    close_time: datetime | None
    url: str | None


@router.get("/{market_ticker}/outcomes", response_model=list[OutcomeOut])
async def market_outcomes(market_ticker: str, engine: AsyncEngine = Depends(get_db_engine)) -> list[OutcomeOut]:
    """Every active market of the same event as this one, this one included, by outcome label.
    An event ("What will Jensen Huang say?") is one question with one market per answer, and the
    chart draws one answer's odds; this is the list to pick another answer from. A market whose
    event is unknown is its own only outcome.
    """
    async with engine.connect() as conn:
        me = (await conn.execute(select(Market.source, Market.event_id).where(
            Market.source.in_(VENUES), Market.market_id == market_ticker))).first()
        if me is None:
            raise HTTPException(404, f"Unknown market: {market_ticker}")
        source, event_id = me
        where = ([Market.source == source, Market.event_id == event_id, Market.status == "active"]
                 if event_id else [Market.source == source, Market.market_id == market_ticker])
        rows = (await conn.execute(select(Market.source, Market.market_id, Market.title, Market.outcome_label,
                                          Market.event_title, Market.rules_primary, Market.close_time, Market.url)
                                   .where(*where).order_by(Market.outcome_label.nulls_last(), Market.market_id))).all()
    return [OutcomeOut(**dict(r._mapping)) for r in rows]


class _Feed:
    """One market's price history on its venue: when it opened, and its points for a window.
    Both venues answer with a probability in 0-1, so the route treats them alike.
    """
    open_time: datetime | None

    async def points(self, start_ts: int, end_ts: int, interval_min: int) -> list[Point]:
        raise NotImplementedError


class _KalshiFeed(_Feed):
    def __init__(self, client: httpx.AsyncClient, series: str, ticker: str):
        self.client, self.series, self.ticker = client, series, ticker

    async def load(self) -> "_KalshiFeed":
        # The markets table has no open time; Kalshi's market endpoint does.
        self.open_time = _parse_time((await fetch_market(self.client, self.ticker)).get("open_time"))
        return self

    async def points(self, start_ts, end_ts, interval_min):
        return market_points(await fetch_candles(self.client, self.series, self.ticker, start_ts, end_ts,
                                                 interval_min))


class _PolymarketFeed(_Feed):
    def __init__(self, client: httpx.AsyncClient, condition_id: str):
        self.client, self.condition_id = client, condition_id
        self.token: str | None = None

    async def load(self) -> "_PolymarketFeed":
        # Prices are per outcome token, and the markets table keeps only the conditionId, so the
        # token and the start date both come from Gamma.
        market = await polymarket.fetch_market(self.client, self.condition_id)
        self.token = polymarket.yes_token(market)
        self.open_time = _parse_time(market.get("startDate") or market.get("createdAt"))
        return self

    async def points(self, start_ts, end_ts, interval_min):
        if self.token is None:
            return []
        history = await polymarket.fetch_price_history(self.client, self.token, start_ts, end_ts, interval_min)
        # Polymarket's price history carries no volume, so these points leave it None.
        return [Point(t=int(p["t"]), close=float(p["p"])) for p in history]


@router.get("/{market_ticker}/history", response_model=HistoryOut)
async def market_history(market_ticker: str, stock: str = Query(min_length=1, max_length=10),
                         range_: Range = Query("1D", alias="range"),
                         end: str | None = Query(None, description="ISO 8601 window end; defaults to now"),
                         engine: AsyncEngine = Depends(get_db_engine),
                         alpaca: AlpacaApiGateway = Depends(get_alpaca_gateway)):
    """The market's and the stock's close prices for one range, at the same interval, in the window
    ending at `end` (now by default). The 1D window steps back to the last session with stock bars,
    so asking on a Sunday shows Friday; `window_end` says where it landed.
    """
    venue = await _venue(engine, market_ticker)
    if venue is None:
        raise HTTPException(404, f"Unknown market: {market_ticker}")
    source, series = venue
    try:
        now = parse_end(end)
    except ValueError as exc:
        raise HTTPException(400, f"Bad end: {exc}") from exc
    if range_ == "1D":
        now = snap_to_session(now)
    async with httpx.AsyncClient(base_url=REST_URL, timeout=15) as client:
        feed = (_KalshiFeed(client, series, market_ticker) if source == "kalshi"
                else _PolymarketFeed(client, market_ticker))
        try:
            open_time = (await feed.load()).open_time
            if open_time is None:
                raise HTTPException(502, f"{source} gave no open time for {market_ticker}")
            ranges = available_ranges(open_time, now)
            if range_ not in ranges:
                raise HTTPException(400, f"{market_ticker} is too new for {range_}; available: {', '.join(ranges)}")
            _, interval_min, timeframe = RANGES[range_]
            # Intraday only: a day the exchange was closed has no bars, so try the day before (holidays).
            for _ in range(MAX_STEPS_BACK + 1):
                start, end_at = window(range_, open_time, now)
                try:
                    bars = await asyncio.to_thread(alpaca.fetch_bars, stock.upper(), timeframe, start, end_at)
                except AlpacaApiError as exc:
                    raise HTTPException(502 if exc.retryable else 400, exc.message) from exc
                if bars or range_ != "1D" or previous_day(now) < open_time:
                    break
                now = previous_day(now)
            market = await feed.points(int(max(start, open_time).timestamp()), int(end_at.timestamp()),
                                       interval_min)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"{source} request failed: {exc}") from exc
    return HistoryOut(market_ticker=market_ticker, stock_ticker=stock.upper(), range=range_,
                      interval=INTERVAL_LABEL[interval_min], available_ranges=ranges,
                      market=market, stock=stock_points(bars, stock.upper(), interval_min),
                      window_end=end_at, open_time=open_time)
