import type { ChartRange, NormalizedPoint, NormalizedSeries } from "../../types/market";
import { get } from "../apiClient";
import { alignAndNormalize, type RawPoint, type RawSeries } from "../chart/align";

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
/**
 * A market younger than this is sampled at one-minute bars (the 1D range) for the correlation,
 * whatever range is displayed. The relationship needs 25 return pairs, and a pair only forms where
 * a stock bar and a market candle share a timestamp; at the hourly interval of 1W and 1M that is
 * about 7 pairs per session, so a market needs about four sessions (a week) before hourly pairs
 * can say anything. One session at one-minute bars gives hundreds.
 */
export const YOUNG_MARKET_MS = 7 * DAY_MS;
/** Window length per range; "all" is open-ended (the market's whole life), so it isn't trimmed. */
const RANGE_DAYS: Record<ChartRange, number> = { "1d": 1, "1w": 7, "1m": 30, "6m": 182, "1y": 365, all: Number.POSITIVE_INFINITY };
/** Mock data needs a finite window; "all" mocks a year. */
const MOCK_DAYS: Record<ChartRange, number> = { ...RANGE_DAYS, all: 365 };
/** Stock-only fallback (no market succeeded): the closest existing Alpaca tier for each range. */
const STOCK_TIER: Record<ChartRange, string> = {
  "1d": "daily",
  "1w": "weekly",
  "1m": "monthly",
  "6m": "quarterly", // 90 days is the longest tier below all_time; trimmed client-side
  "1y": "all_time",
  all: "all_time",
};
/** Fallback for GET /markets/{id}/series, which only knows 1w | 1m | 3m. */
const LEGACY_SERIES_RANGE: Record<ChartRange, string> = { "1d": "1w", "1w": "1w", "1m": "1m", "6m": "3m", "1y": "3m", all: "3m" };
/** Market proxy used to strip the market-wide move out of the stock's returns (see lib/chart/relationship.ts). */
export const BENCHMARK_TICKER = "SPY";

/** The instant a calendar day ends: 00:00 UTC of the next day, which is what the backend's windows expect. */
export function dayEnd(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString();
}

/** Both endpoints return rows of this shape (price_or_odds is USD close for stocks, 0-100 for markets). */
interface SeriesRow {
  source: string;
  market_id: string;
  price_or_odds: number;
  timestamp: string; // ISO 8601
}

function toRawPoints(rows: SeriesRow[]): RawPoint[] {
  return rows
    .map((row) => ({ t: Date.parse(row.timestamp), value: row.price_or_odds }))
    .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.value));
}

/** GET /markets/{ticker}/history (backend/api/market_history.py): stock and market at one interval. */
interface HistoryResponse {
  interval: string; // '1m' | '1h' | '1d'
  available_ranges: string[]; // upper-case, e.g. ["1D", "1W", "ALL"]
  // t: Unix seconds; close: probability 0-1 (market) or USD (stock); volume: contracts (Kalshi) or
  // shares (Alpaca) traded in the candle, null for venues without one (Polymarket); older backends omit it.
  market: { t: number; close: number; volume?: number | null }[];
  stock: { t: number; close: number; volume?: number | null }[];
  open_time?: string; // ISO 8601: when the market opened on its venue (older backends omit it)
  window_end?: string; // ISO 8601: where the window ended (a weekend 1D snaps to Friday)
}

/** "3 hours", "2 days": a market's age, for the chart's notes. */
export function describeAge(ms: number): string {
  if (ms < HOUR_MS) return "under an hour";
  if (ms < DAY_MS) {
    const h = Math.round(ms / HOUR_MS);
    return `${h} hour${h === 1 ? "" : "s"}`;
  }
  const d = Math.round(ms / DAY_MS);
  return `${d} day${d === 1 ? "" : "s"}`;
}

/** Seconds -> ms, and the venue's 0-1 probability -> the chart's 0-100 odds. */
function historyMarketPoints(h: HistoryResponse): RawPoint[] {
  return h.market.map((p) => historyPoint(p.t, p.close * 100, p.volume));
}
function historyStockPoints(h: HistoryResponse): RawPoint[] {
  return h.stock.map((p) => historyPoint(p.t, p.close, p.volume));
}
/** A null/absent volume stays absent (not 0): the chart shows no bar, not an empty one. */
function historyPoint(seconds: number, value: number, volume: number | null | undefined): RawPoint {
  return volume == null ? { t: seconds * 1000, value } : { t: seconds * 1000, value, volume };
}

/** Ranges every selected market can show. A range is only offered if no market is "too new" for it. */
export function commonRanges(responses: HistoryResponse[]): ChartRange[] | null {
  if (responses.length === 0) return null;
  const sets = responses.map((r) => new Set(r.available_ranges.map((x) => x.toLowerCase())));
  return (Object.keys(RANGE_DAYS) as ChartRange[]).filter((range) => sets.every((set) => set.has(range)));
}

/** Mock stock: weekday closes only (like a real exchange), so the gaps over weekends are realistic. */
function mockStockPoints(range: ChartRange, seed: number, end: number, startPrice = 182, step = 1.5): RawPoint[] {
  const days = MOCK_DAYS[range];
  const stepMs = range === "1d" ? DAY_MS / 24 : range === "1w" ? DAY_MS / 4 : DAY_MS;
  const points: RawPoint[] = [];
  let price = startPrice;
  for (let t = end - days * DAY_MS, i = 0; t <= end; t += stepMs, i += 1) {
    const day = new Date(t).getUTCDay();
    if (day === 0 || day === 6) continue;
    price = Math.max(1, price + Math.sin(seed + i * 0.6) * step);
    points.push({ t, value: price });
  }
  return points;
}

function marketFallbackLabel(id: string): string {
  return id.replace(/^KX-?/, "").replace(/-/g, " ");
}

export interface MarketMeta {
  /** When the market opened on its venue (epoch ms); null when the history route didn't say. */
  openTime: number | null;
}

/**
 * Series the correlation is computed from when they differ from the displayed ones: a young
 * market's last session at one-minute bars (see YOUNG_MARKET_MS).
 */
export interface CorrelationInput {
  stock: NormalizedPoint[];
  benchmark: NormalizedPoint[] | null;
  market: NormalizedPoint[];
  interval: string; // '1m' | '1h' | '1d'
  /** One sentence for the panel on why this sampling was used. */
  reason: string;
}

export interface SeriesBundle {
  /** What the charts display: the stock first, then one series per market. */
  series: NormalizedSeries[];
  /** SPY on the same grid, for return adjustment only. Never displayed. Null when the ticker is SPY itself. */
  benchmark: NormalizedSeries | null;
  /** Ranges every selected market has history for; null when none came from the history route. */
  availableRanges: ChartRange[] | null;
  /** Sampling interval of the displayed series ('1m' | '1h' | '1d'); null when no history route answered. */
  interval: string | null;
  /**
   * The window the range asked for, [start, end] in ms: the backend's window end and the
   * range's length back from it ("all": from the earliest point). The chart's x-axis spans
   * this, not the data, so 1D is a day and 1W a week even where the stock has no bars.
   * Null when no history route answered (the legacy routes don't say where their window ended).
   */
  window: [number, number] | null;
  /** Per market id: what the history route said about the market itself. */
  markets: Record<string, MarketMeta>;
  /** Per market id: a finer-sampled set of series for the correlation, only for markets that need one. */
  correlation: Record<string, CorrelationInput>;
}

/** Convenience wrapper for callers that only need the displayed series. */
export async function fetchNormalizedSeries(
  ticker: string,
  marketIds: string[],
  range: ChartRange,
  labels: Record<string, string> = {},
): Promise<NormalizedSeries[]> {
  return (await fetchSeriesBundle(ticker, marketIds, range, labels)).series;
}

/**
 * Stock + market series (+ SPY benchmark) for the overlay chart, aligned and normalized.
 * A market's odds are only ever the venue's own history, fetched live by the backend (Kalshi
 * candlesticks, Polymarket price history). A market with none comes back empty and the chart
 * says so; it never draws a made-up line. The stock and SPY still fall back to a mock walk
 * when Alpaca can't be reached.
 */
export async function fetchSeriesBundle(
  ticker: string,
  marketIds: string[],
  range: ChartRange,
  /** marketId -> card title; falls back to a prettified id. */
  labels: Record<string, string> = {},
  /** Calendar day ("YYYY-MM-DD") the window should end on; null/undefined = now. */
  day: string | null = null,
): Promise<SeriesBundle> {
  // The window end the backend is asked for: the end of `day` (00:00 UTC the next day), or now.
  // Mocks share the same instant so the stock and SPY fallbacks land on identical timestamps.
  const endIso = day ? dayEnd(day) : null;
  const now = endIso ? Date.parse(endIso) : Date.now();
  const endParam = endIso ? `&end=${encodeURIComponent(endIso)}` : "";
  const upperRange = range.toUpperCase();
  const wantBenchmark = ticker.toUpperCase() !== BENCHMARK_TICKER;

  // Preferred path, one call per market: the market's candles AND the stock's bars at the same
  // interval and window, so the two lines are sampled on the same clock. A market the route
  // can't serve (not in the DB, too new for this range, backend down) falls back.
  const histories = await Promise.all(
    marketIds.map((id) =>
      get<HistoryResponse>(
        `/markets/${encodeURIComponent(id)}/history?stock=${encodeURIComponent(ticker)}&range=${upperRange}${endParam}`,
      ).catch(() => null),
    ),
  );
  const firstHistory = histories.find((h): h is HistoryResponse => h !== null) ?? null;
  const availableRanges = commonRanges(histories.filter((h): h is HistoryResponse => h !== null));
  const marketMeta: Record<string, MarketMeta> = {};
  histories.forEach((h, i) => {
    if (!h) return;
    const open = h.open_time ? Date.parse(h.open_time) : NaN;
    marketMeta[marketIds[i]] = { openTime: Number.isFinite(open) ? open : null };
  });
  // Started now so it runs alongside the stock and SPY fetches below.
  const correlation = fineCorrelationInputs(ticker, marketIds, marketMeta, range, now, endParam, wantBenchmark);

  const stockPoints: Promise<RawPoint[]> = firstHistory
    ? Promise.resolve(historyStockPoints(firstHistory))
    : get<SeriesRow[]>(`/stocks/${encodeURIComponent(ticker)}/prices?tier=${STOCK_TIER[range]}${endParam}`)
        .then(toRawPoints)
        .catch(() => mockStockPoints(range, 1, now));

  // SPY must share the stock's interval or the return pairs misalign, so when the stock came from
  // the history route, SPY does too (same window, same interval; only its `stock` half is used).
  const benchmarkPoints: Promise<RawPoint[]> = !wantBenchmark
    ? Promise.resolve([])
    : firstHistory
      ? get<HistoryResponse>(
          `/markets/${encodeURIComponent(marketIds[histories.indexOf(firstHistory)])}/history?stock=${BENCHMARK_TICKER}&range=${upperRange}${endParam}`,
        )
          .then(historyStockPoints)
          .catch(() => mockStockPoints(range, 7, now, 520, 3))
      : get<SeriesRow[]>(`/stocks/${BENCHMARK_TICKER}/prices?tier=${STOCK_TIER[range]}${endParam}`)
          .then(toRawPoints)
          .catch(() => mockStockPoints(range, 7, now, 520, 3)); // different walk from the stock's

  const marketPoints = marketIds.map((id, idx) => {
    const history = histories[idx];
    if (history) return Promise.resolve(historyMarketPoints(history));
    // mock=false: without it this route answers with a generated series when Kalshi has nothing.
    return get<SeriesRow[]>(`/markets/${encodeURIComponent(id)}/series?range=${LEGACY_SERIES_RANGE[range]}&mock=false`)
      .then(toRawPoints)
      .catch((): RawPoint[] => []);
  });

  const [stock, benchmark, fine, ...markets] = await Promise.all([stockPoints, benchmarkPoints, correlation, ...marketPoints]);

  // Trim the stock to the range relative to its newest point (not "now"), so stale data still shows.
  const stockEnd = stock.reduce((max, p) => Math.max(max, p.t), 0);
  const windowStart = stockEnd - RANGE_DAYS[range] * DAY_MS; // -Infinity for "all": keep everything
  const stockInRange = stock.filter((p) => p.t >= windowStart);
  const benchmarkInRange = benchmark.filter((p) => p.t >= windowStart && p.t <= stockEnd);

  const raw: RawSeries[] = [
    { id: ticker, label: ticker.toUpperCase(), kind: "stock", unit: "usd", points: stockInRange },
    // Second "stock" is fine: align.ts takes its window from the FIRST stock with data. Caveat: its
    // timestamps join the shared grid, so a benchmark with extra ticks adds forward-filled rows to the others.
    ...(benchmarkInRange.length > 0
      ? [{ id: BENCHMARK_TICKER, label: BENCHMARK_TICKER, kind: "stock", unit: "usd", points: benchmarkInRange } as RawSeries]
      : []),
    ...marketIds.map((id, i): RawSeries => ({
      id,
      label: labels[id] ?? marketFallbackLabel(id),
      kind: "market",
      unit: "probability",
      points: markets[i],
    })),
  ];
  const aligned = alignAndNormalize(raw);
  const benchmarkSeries = wantBenchmark ? aligned.find((s) => s.id === BENCHMARK_TICKER) ?? null : null;
  return {
    series: aligned.filter((s) => s !== benchmarkSeries),
    benchmark: benchmarkSeries,
    availableRanges,
    interval: firstHistory?.interval ?? null,
    window: requestedWindow(firstHistory, range, aligned),
    markets: marketMeta,
    correlation: fine,
  };
}

function requestedWindow(history: HistoryResponse | null, range: ChartRange, series: NormalizedSeries[]): [number, number] | null {
  const end = history?.window_end ? Date.parse(history.window_end) : NaN;
  if (!Number.isFinite(end)) return null;
  if (range === "all") {
    const first = Math.min(...series.flatMap((s) => (s.points.length > 0 ? [s.points[0].t] : [])));
    return Number.isFinite(first) && first < end ? [first, end] : null;
  }
  return [end - RANGE_DAYS[range] * DAY_MS, end];
}

/**
 * For each market younger than YOUNG_MARKET_MS while a coarser range is shown: its last session
 * at one-minute bars (the 1D range), with the stock and SPY on the same clock, aligned for the
 * correlation. SPY is fetched once: the 1D window is the same for every market. A market whose
 * 1D history can't be read is left out and the panel falls back to the displayed series.
 */
async function fineCorrelationInputs(
  ticker: string,
  marketIds: string[],
  meta: Record<string, MarketMeta>,
  range: ChartRange,
  now: number,
  endParam: string,
  wantBenchmark: boolean,
): Promise<Record<string, CorrelationInput>> {
  const out: Record<string, CorrelationInput> = {};
  if (range === "1d") return out;
  const young = marketIds.filter((id) => {
    const open = meta[id]?.openTime;
    return open !== null && open !== undefined && now - open < YOUNG_MARKET_MS;
  });
  if (young.length === 0) return out;

  const fineOf = (id: string, symbol: string) =>
    get<HistoryResponse>(
      `/markets/${encodeURIComponent(id)}/history?stock=${encodeURIComponent(symbol)}&range=1D${endParam}`,
    ).catch(() => null);
  const [spy, ...histories] = await Promise.all([
    wantBenchmark ? fineOf(young[0], BENCHMARK_TICKER) : Promise.resolve(null),
    ...young.map((id) => fineOf(id, ticker)),
  ]);

  young.forEach((id, i) => {
    const h = histories[i];
    if (!h || h.stock.length === 0) return;
    const raw: RawSeries[] = [
      { id: ticker, label: ticker, kind: "stock", unit: "usd", points: historyStockPoints(h) },
      ...(spy ? [{ id: BENCHMARK_TICKER, label: BENCHMARK_TICKER, kind: "stock", unit: "usd", points: historyStockPoints(spy) } as RawSeries] : []),
      { id, label: id, kind: "market", unit: "probability", points: historyMarketPoints(h) },
    ];
    const aligned = alignAndNormalize(raw);
    const age = describeAge(now - (meta[id].openTime as number));
    out[id] = {
      stock: aligned[0].points,
      benchmark: spy ? aligned[1].points : null,
      market: aligned[aligned.length - 1].points,
      interval: h.interval,
      reason: `Sampled at ${h.interval} bars over the last session: this market opened ${age} ago, too young for ${range} bars to give enough return pairs.`,
    };
  });
  return out;
}
