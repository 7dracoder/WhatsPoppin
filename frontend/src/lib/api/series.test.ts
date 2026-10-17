import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../apiClient";

vi.mock("../apiClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../apiClient")>()),
  get: vi.fn(),
}));

import { get } from "../apiClient";
import { commonRanges, fetchSeriesBundle } from "./series";

const mockGet = vi.mocked(get);

const T0 = 1_760_000_000; // Unix seconds
const WINDOW_END = new Date((T0 + 4 * 3600) * 1000).toISOString();
function history(available: string[], marketClose = 0.4, stockBase = 100, openTime?: string, interval = "1h") {
  return {
    interval,
    open_time: openTime,
    window_end: WINDOW_END,
    available_ranges: available,
    market: [
      { t: T0, close: marketClose, volume: 57.25 },
      { t: T0 + 7200, close: marketClose + 0.1, volume: null }, // Polymarket-style: no volume feed
    ],
    stock: [0, 1, 2, 3].map((i) => ({ t: T0 + i * 3600, close: stockBase + i, volume: 1000 * (i + 1) })),
  };
}

describe("commonRanges", () => {
  it("keeps only ranges every market has, in range order", () => {
    const a = history(["1D", "1W", "ALL"]);
    const b = history(["1D", "1W", "1M", "ALL"]);
    expect(commonRanges([a, b])).toEqual(["1d", "1w", "all"]);
  });
  it("is null with no history responses", () => {
    expect(commonRanges([])).toBeNull();
  });
});

describe("fetchSeriesBundle", () => {
  beforeEach(() => {
    mockGet.mockReset(); // braces: a returned function would be run by vitest as a teardown hook
  });

  it("uses the history route: seconds -> ms, 0-1 -> 0-100, SPY from the same route", async () => {
    mockGet.mockImplementation(async (path: string) => {
      if (path.includes("stock=SPY")) return history(["1W"], 0.4, 500);
      return history(["1W", "ALL"]);
    });
    const bundle = await fetchSeriesBundle("NVDA", ["KXFED-26DEC"], "1w");

    const paths = mockGet.mock.calls.map((c) => c[0]);
    expect(paths).toContain("/markets/KXFED-26DEC/history?stock=NVDA&range=1W");
    expect(paths).toContain("/markets/KXFED-26DEC/history?stock=SPY&range=1W");
    expect(paths.some((p) => p.startsWith("/stocks/"))).toBe(false); // no coarser tier mixed in

    const market = bundle.series.find((s) => s.kind === "market")!;
    expect(market.points[0].rawValue).toBeCloseTo(40); // 0.4 -> 40 odds points
    expect(market.points[0].t).toBe(T0 * 1000);
    // Volume rides along per bar; filled grid steps (the stock-only ticks at +1h, +3h) and a null stay undefined.
    expect(market.points.map((p) => p.volume)).toEqual([57.25, undefined, undefined, undefined]);
    const stock = bundle.series.find((s) => s.kind === "stock")!;
    expect(stock.points.map((p) => p.volume)).toEqual([1000, 2000, 3000, 4000]);
    expect(bundle.benchmark).not.toBeNull();
    expect(bundle.availableRanges).toEqual(["1w", "all"]);
    // The axis window is the week the range asked for, ending where the backend's window did.
    const end = Date.parse(WINDOW_END);
    expect(bundle.window).toEqual([end - 7 * 86_400_000, end]);
  });

  it("samples a market younger than a week at the 1D range for the correlation, SPY once", async () => {
    const openTime = new Date(Date.now() - 36 * 3_600_000).toISOString();
    mockGet.mockImplementation(async (path: string) => {
      const fine = path.includes("range=1D");
      if (path.includes("stock=SPY")) return history(["1D", "1W", "ALL"], 0.4, 500, openTime, fine ? "1m" : "1h");
      return history(["1D", "1W", "ALL"], 0.4, 100, openTime, fine ? "1m" : "1h");
    });
    const bundle = await fetchSeriesBundle("NVDA", ["KXGAS-1", "KXGAS-2"], "1w");

    const paths = mockGet.mock.calls.map((c) => c[0]);
    expect(paths.filter((p) => p.includes("range=1D") && p.includes("stock=NVDA"))).toHaveLength(2);
    expect(paths.filter((p) => p.includes("range=1D") && p.includes("stock=SPY"))).toHaveLength(1);
    expect(bundle.interval).toBe("1h");
    expect(bundle.markets["KXGAS-1"].openTime).toBe(Date.parse(openTime));
    const fine = bundle.correlation["KXGAS-1"];
    expect(fine.interval).toBe("1m");
    expect(fine.benchmark).not.toBeNull();
    expect(fine.market[0].rawValue).toBeCloseTo(40);
    expect(fine.reason).toMatch(/opened 2 days ago/);
  });

  it("leaves the correlation to the displayed series for an older market or the 1D range", async () => {
    const old = new Date(Date.now() - 30 * 86_400_000).toISOString();
    mockGet.mockImplementation(async () => history(["1D", "1W", "1M", "ALL"], 0.4, 100, old));
    expect((await fetchSeriesBundle("NVDA", ["KXFED"], "1w")).correlation).toEqual({});
    const young = new Date(Date.now() - 3_600_000).toISOString();
    mockGet.mockImplementation(async () => history(["1D", "ALL"], 0.4, 100, young, "1m"));
    expect((await fetchSeriesBundle("NVDA", ["KXFED"], "1d")).correlation).toEqual({});
  });

  it("falls back to the legacy series route for a market the history route can't serve", async () => {
    mockGet.mockImplementation(async (path: string) => {
      if (path.includes("/history")) throw new ApiError(404, "Unknown Kalshi market");
      if (path.startsWith("/markets/")) {
        return [{ source: "kalshi", market_id: "X", price_or_odds: 55, timestamp: new Date(T0 * 1000).toISOString() }];
      }
      return [0, 1, 2].map((i) => ({
        source: "alpaca", market_id: "NVDA", price_or_odds: 100 + i, timestamp: new Date((T0 + i * 3600) * 1000).toISOString(),
      }));
    });
    const bundle = await fetchSeriesBundle("NVDA", ["poly-slug"], "6m");
    const paths = mockGet.mock.calls.map((c) => c[0]);
    expect(paths).toContain("/markets/poly-slug/series?range=3m&mock=false"); // 6m maps to the longest legacy range
    expect(paths).toContain("/stocks/NVDA/prices?tier=quarterly");
    expect(bundle.availableRanges).toBeNull();
    expect(bundle.series.some((s) => s.kind === "market" && s.points.length > 0)).toBe(true);
  });

  it("leaves a market empty rather than inventing a line when no route has its history", async () => {
    mockGet.mockImplementation(async (path: string) => {
      if (path.startsWith("/markets/")) throw new ApiError(404, "No price history");
      return [0, 1, 2].map((i) => ({
        source: "alpaca", market_id: "NVDA", price_or_odds: 100 + i, timestamp: new Date((T0 + i * 3600) * 1000).toISOString(),
      }));
    });
    const bundle = await fetchSeriesBundle("NVDA", ["unknown-market"], "1m");
    const market = bundle.series.find((s) => s.kind === "market")!;
    expect(market.points).toEqual([]);
  });
});
