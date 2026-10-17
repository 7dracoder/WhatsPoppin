import { describe, expect, it } from "vitest";
import { fitOddsDomain, formatVolume, volumeDomain, timeTicks, tickFormatterFor } from "./axes";

describe("fitOddsDomain", () => {
  it("pads the data by 10% of its span on each side, rounded outward to whole points", () => {
    expect(fitOddsDomain([40, 60])).toEqual([38, 62]);
  });
  it("gives a flat market at least a 2-point band instead of a zero-height one", () => {
    expect(fitOddsDomain([50, 50])).toEqual([49, 51]);
    expect(fitOddsDomain([41.2, 41.7])).toEqual([40, 43]);
  });
  it("never leaves the 0-100 a probability can occupy", () => {
    expect(fitOddsDomain([0.5, 3])).toEqual([0, 4]);
    expect(fitOddsDomain([97, 99.5])).toEqual([96, 100]);
  });
  it("falls back to the full scale with no data", () => {
    expect(fitOddsDomain([])).toEqual([0, 100]);
  });
});

describe("volumeDomain", () => {
  it("leaves headroom so the tallest bar fills a quarter of the panel", () => {
    expect(volumeDomain([10, undefined, 40, 25])).toEqual([0, 160]);
  });
  it("is a non-degenerate [0, 1] when there is no volume at all", () => {
    expect(volumeDomain([])).toEqual([0, 1]);
    expect(volumeDomain([undefined, 0])).toEqual([0, 1]);
  });
});

describe("formatVolume", () => {
  it("compacts thousands, millions and billions to one decimal", () => {
    expect(formatVolume(57.25)).toBe("57.3");
    expect(formatVolume(1200)).toBe("1.2K");
    expect(formatVolume(125000)).toBe("125K");
    expect(formatVolume(2_500_000)).toBe("2.5M");
    expect(formatVolume(3_000_000_000)).toBe("3B");
  });
});

describe("timeTicks", () => {
  const local = (y: number, m: number, d: number, h = 0, min = 0) => new Date(y, m - 1, d, h, min).getTime();

  it("puts a one-day window's ticks on whole hours", () => {
    const ticks = timeTicks([local(2026, 10, 9, 9, 30), local(2026, 10, 9, 16, 0)]);
    expect(ticks[0]).toBe(local(2026, 10, 9, 10));
    expect(ticks.every((t) => new Date(t).getMinutes() === 0)).toBe(true);
    expect(ticks.length).toBeLessThanOrEqual(6);
    expect(ticks.length).toBeGreaterThanOrEqual(3);
  });

  it("puts a week's ticks on local midnights, one per day", () => {
    const ticks = timeTicks([local(2026, 10, 2, 16), local(2026, 10, 9, 16)], 8);
    expect(ticks).toEqual([3, 4, 5, 6, 7, 8, 9].map((d) => local(2026, 10, d)));
  });

  it("thins a long window to a few ticks and copes with an empty one", () => {
    expect(timeTicks([local(2026, 1, 1), local(2026, 12, 31)]).length).toBeLessThanOrEqual(6);
    expect(timeTicks([5, 5])).toEqual([]);
  });
});

describe("tickFormatterFor", () => {
  it("labels by span: times within a day, dates within a year, months beyond", () => {
    const t = new Date(2026, 9, 9, 10, 30).getTime();
    expect(tickFormatterFor([t, t + 3_600_000])(t)).toMatch(/10:30/);
    expect(tickFormatterFor([t, t + 7 * 86_400_000])(t)).toMatch(/Oct/);
    expect(tickFormatterFor([t, t + 7 * 86_400_000])(t)).not.toMatch(/10:30/);
    expect(tickFormatterFor([t, t + 800 * 86_400_000])(t)).toMatch(/26/);
  });
});
