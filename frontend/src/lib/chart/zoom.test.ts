import { describe, expect, it } from "vitest";
import { brushDomain, pixelToTime, within, zoomAround } from "./zoom";

const FULL: [number, number] = [1000, 2000];

describe("pixelToTime", () => {
  it("maps the plot's left and right edges to the domain's ends, linearly between", () => {
    expect(pixelToTime(56, 56, 456, FULL)).toBe(1000);
    expect(pixelToTime(456, 56, 456, FULL)).toBe(2000);
    expect(pixelToTime(256, 56, 456, FULL)).toBe(1500);
  });
  it("clamps pixels outside the plot to its edges", () => {
    expect(pixelToTime(0, 56, 456, FULL)).toBe(1000);
    expect(pixelToTime(900, 56, 456, FULL)).toBe(2000);
  });
  it("is the domain start for a zero-width plot rather than NaN", () => {
    expect(pixelToTime(10, 56, 56, FULL)).toBe(1000);
  });
});

describe("brushDomain", () => {
  it("orders the ends, whichever way the user dragged", () => {
    expect(brushDomain(1600, 1200, FULL)).toEqual([1200, 1600]);
  });
  it("keeps the selection inside the full window", () => {
    expect(brushDomain(500, 1500, FULL)).toEqual([1000, 1500]);
  });
  it("treats a tiny drag as a click", () => {
    expect(brushDomain(1500, 1505, FULL)).toBeNull();
  });
});

describe("zoomAround", () => {
  it("halves the span keeping the anchor at the same screen fraction", () => {
    // anchor at 25% of [1000, 2000]; after halving it must still sit 25% in.
    expect(zoomAround(FULL, FULL, 1250, 0.5)).toEqual([1125, 1625]);
  });
  it("slides the window back inside the data instead of overflowing an edge", () => {
    expect(zoomAround([1000, 1400], FULL, 1000, 2)).toEqual([1000, 1800]);
    expect(zoomAround([1600, 2000], FULL, 2000, 2)).toEqual([1200, 2000]);
  });
  it("returns null (full window) once zoomed all the way out", () => {
    expect(zoomAround([1200, 1800], FULL, 1500, 3)).toBeNull();
  });
  it("stops zooming in at the minimum span", () => {
    const [lo, hi] = zoomAround([1500, 1501], FULL, 1500.5, 0.1)!;
    expect(hi - lo).toBeCloseTo(10); // 1% of the full window
  });
});

describe("within", () => {
  it("keeps points inside the domain, ends inclusive", () => {
    const pts = [{ t: 999 }, { t: 1000 }, { t: 1500 }, { t: 2000 }, { t: 2001 }];
    expect(within(pts, FULL).map((p) => p.t)).toEqual([1000, 1500, 2000]);
  });
});
