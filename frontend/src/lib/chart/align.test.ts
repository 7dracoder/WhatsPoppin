import { describe, expect, it } from "vitest";
import { alignAndNormalize, type RawSeries } from "./align";

const stock = (points: Array<[number, number]>): RawSeries => ({
  id: "AAPL", label: "AAPL", kind: "stock", unit: "usd", points: points.map(([t, value]) => ({ t, value })),
});
const market = (points: Array<[number, number]>, id = "M1"): RawSeries => ({
  id, label: id, kind: "market", unit: "probability", points: points.map(([t, value]) => ({ t, value })),
});

describe("alignAndNormalize", () => {
  it("normalizes stock as % change and odds as percentage-point change", () => {
    const [s, m] = alignAndNormalize([stock([[1, 100], [2, 110]]), market([[1, 3], [2, 6]])]);
    expect(s.points.map((p) => p.value)).toEqual([0, 10]);
    expect(m.points.map((p) => p.value)).toEqual([0, 3]); // 3 pts, not +100%
    expect(m.points[1].rawValue).toBe(6);
  });

  it("forward-fills a series across grid timestamps it has no point on", () => {
    const [, m] = alignAndNormalize([stock([[1, 100], [2, 101], [3, 102]]), market([[1, 40], [3, 50]])]);
    expect(m.points.map((p) => [p.t, p.rawValue])).toEqual([[1, 40], [2, 40], [3, 50]]);
  });

  it("spans the union of the series: trades outside the stock's hours still draw, and the stock holds through them", () => {
    const [s, m] = alignAndNormalize([
      stock([[10, 100], [20, 100]]),
      market([[5, 40], [15, 45], [30, 50]]),
    ]);
    // The stock starts at its own first bar (no back-fill) and holds its last close past it.
    expect(s.points.map((p) => [p.t, p.rawValue])).toEqual([[10, 100], [15, 100], [20, 100], [30, 100]]);
    expect(m.points.map((p) => [p.t, p.value])).toEqual([[5, 0], [10, 0], [15, 5], [20, 5], [30, 10]]);
  });

  it("starts a late market at its own first value instead of back-filling", () => {
    const [, m] = alignAndNormalize([stock([[1, 100], [2, 100], [3, 100]]), market([[2, 20], [3, 25]])]);
    expect(m.points.map((p) => [p.t, p.value])).toEqual([[2, 0], [3, 5]]);
  });

  it("returns an empty series when a market has no points at all", () => {
    const [, m] = alignAndNormalize([stock([[10, 100], [20, 101]]), market([])]);
    expect(m.points).toEqual([]);
  });

  it("carries volume only on the grid step its bar ends on, never forward-filled", () => {
    const [s, m] = alignAndNormalize([
      { ...stock([[1, 100], [3, 101]]), points: [{ t: 1, value: 100, volume: 500 }, { t: 3, value: 101, volume: 700 }] },
      market([[1, 40], [2, 45]]), // no volume feed at all (Polymarket)
    ]);
    expect(s.points.map((p) => [p.t, p.rawValue, p.volume])).toEqual([[1, 100, 500], [2, 100, undefined], [3, 101, 700]]);
    expect(s.points[1]).not.toHaveProperty("volume"); // filled step: no key, not volume: 0
    expect(m.points.every((p) => p.volume === undefined)).toBe(true);
  });

  it("handles unsorted input and no stock data", () => {
    const [m] = alignAndNormalize([market([[3, 30], [1, 10]])]);
    expect(m.points.map((p) => p.value)).toEqual([0, 20]);
    expect(alignAndNormalize([stock([])])[0].points).toEqual([]);
  });
});
