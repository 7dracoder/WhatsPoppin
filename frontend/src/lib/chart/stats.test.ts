import { describe, expect, it } from "vitest";
import {
  correlationCI,
  crossCorrelation,
  deltaLogit,
  logit,
  logReturns,
  pearson,
  residualizeOnBenchmark,
  rollingCorrelation,
} from "./stats";

const allFinite = (xs: Array<number | null>) => xs.every((x) => x === null || Number.isFinite(x));

// Deterministic pseudo-random noise (no Math.random, so tests are stable).
const noise = (n: number) => {
  let s = 12345;
  return Array.from({ length: n }, () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296 - 0.5;
  });
};

describe("logReturns", () => {
  it("computes ln(P_t / P_{t-1})", () => {
    const r = logReturns([100, 110, 99]);
    expect(r).toHaveLength(2);
    expect(r[0]).toBeCloseTo(Math.log(1.1), 12);
    expect(r[1]).toBeCloseTo(Math.log(0.9), 12);
  });

  it("is additive over time", () => {
    const r = logReturns([50, 60, 45, 80]);
    expect(r.reduce((a, b) => a + b, 0)).toBeCloseTo(Math.log(80 / 50), 12);
  });

  it("guards non-positive and non-finite prices with 0", () => {
    const r = logReturns([100, 0, 100, -5, NaN, 10]);
    expect(r).toEqual([0, 0, 0, 0, 0]);
  });

  it("returns [] for short input", () => {
    expect(logReturns([])).toEqual([]);
    expect(logReturns([5])).toEqual([]);
  });
});

describe("logit / deltaLogit", () => {
  it("is 0 at 50% and antisymmetric", () => {
    expect(logit(50)).toBeCloseTo(0, 12);
    expect(logit(80)).toBeCloseTo(-logit(20), 12);
    expect(logit(75)).toBeCloseTo(Math.log(3), 12);
  });

  it("clamps 0% and 100% to finite values", () => {
    expect(Number.isFinite(logit(0))).toBe(true);
    expect(Number.isFinite(logit(100))).toBe(true);
    expect(logit(0)).toBeCloseTo(Math.log(0.005 / 0.995), 12);
    expect(logit(100)).toBeCloseTo(-logit(0), 12);
    expect(logit(-10)).toBe(logit(0));
  });

  it("deltaLogit gives first differences (length n-1)", () => {
    const d = deltaLogit([50, 75, 75]);
    expect(d).toHaveLength(2);
    expect(d[0]).toBeCloseTo(Math.log(3), 12);
    expect(d[1]).toBe(0);
  });

  it("treats a tail move as larger than a mid-range move of the same points", () => {
    expect(Math.abs(deltaLogit([94, 97])[0])).toBeGreaterThan(Math.abs(deltaLogit([50, 53])[0]));
  });

  it("handles short input and stays finite at the extremes", () => {
    expect(deltaLogit([])).toEqual([]);
    expect(deltaLogit([40])).toEqual([]);
    expect(allFinite(deltaLogit([0, 100, 0]))).toBe(true);
  });
});

describe("pearson", () => {
  it("is 1 / -1 for perfect linear relations", () => {
    expect(pearson([1, 2, 3, 4], [2, 4, 6, 8])).toBeCloseTo(1, 12);
    expect(pearson([1, 2, 3, 4], [8, 6, 4, 2])).toBeCloseTo(-1, 12);
  });

  it("is null for constant or too-short input", () => {
    expect(pearson([1, 1, 1], [1, 2, 3])).toBeNull();
    expect(pearson([1], [2])).toBeNull();
  });
});

describe("residualizeOnBenchmark", () => {
  it("recovers a known alpha and beta exactly on noiseless data", () => {
    const x = [0.01, -0.02, 0.03, 0.0, -0.01];
    const y = x.map((v) => 0.001 + 1.5 * v);
    const { beta, alpha, residuals } = residualizeOnBenchmark(y, x);
    expect(beta).toBeCloseTo(1.5, 10);
    expect(alpha).toBeCloseTo(0.001, 10);
    residuals.forEach((e) => expect(e).toBeCloseTo(0, 10));
  });

  it("known small example: y = [1,2,4], x = [1,2,3] gives beta 1.5, alpha -2/3", () => {
    const { beta, alpha, residuals } = residualizeOnBenchmark([1, 2, 4], [1, 2, 3]);
    expect(beta).toBeCloseTo(1.5, 12);
    expect(alpha).toBeCloseTo(-2 / 3, 12);
    expect(residuals[0]).toBeCloseTo(1 / 6, 12);
  });

  it("residuals are mean zero and uncorrelated with x", () => {
    const x = noise(50);
    const e = noise(100).slice(50).map((v) => v * 0.3); // different draws than x
    const y = x.map((v, i) => 0.5 * v + e[i] + 0.1);
    const { residuals } = residualizeOnBenchmark(y, x);
    expect(residuals.reduce((a, b) => a + b, 0) / 50).toBeCloseTo(0, 10);
    expect(pearson(residuals, x)).toBeCloseTo(0, 10);
  });

  it("handles zero-variance x: beta 0, residuals are demeaned y", () => {
    const { beta, alpha, residuals } = residualizeOnBenchmark([1, 2, 3], [0.5, 0.5, 0.5]);
    expect(beta).toBe(0);
    expect(alpha).toBe(2);
    expect(residuals).toEqual([-1, 0, 1]);
  });

  it("handles empty input without NaN", () => {
    const { beta, alpha, residuals } = residualizeOnBenchmark([], []);
    expect(residuals).toEqual([]);
    expect(Number.isFinite(beta) && Number.isFinite(alpha)).toBe(true);
  });
});

describe("rollingCorrelation", () => {
  it("is null until the window fills", () => {
    const out = rollingCorrelation([1, 2, 3, 4, 5], [2, 4, 6, 8, 10], 3);
    expect(out.slice(0, 2)).toEqual([null, null]);
    expect(out[2]).toBeCloseTo(1, 12);
    expect(out).toHaveLength(5);
  });

  it("uses only the trailing window", () => {
    // First three points rise together, then b falls while a rises.
    const out = rollingCorrelation([1, 2, 3, 4, 5], [1, 2, 3, 2, 1], 3);
    expect(out[2]).toBeCloseTo(1, 12);
    expect(out[4]).toBeCloseTo(-1, 12);
  });

  it("is null where the window has zero variance", () => {
    const out = rollingCorrelation([1, 1, 1, 2], [1, 2, 3, 4], 3);
    expect(out[2]).toBeNull();
    expect(out[3]).not.toBeNull();
  });

  it("returns all null for a series shorter than the window", () => {
    expect(rollingCorrelation([1, 2], [1, 2], 5)).toEqual([null, null]);
  });

  it("never outputs NaN", () => {
    expect(allFinite(rollingCorrelation([3, 3, 3, 3], [1, 2, 3, 4], 2))).toBe(true);
  });
});

describe("correlationCI", () => {
  it("matches a known value (r=0.5, n=103 -> se=0.1)", () => {
    const [lo, hi] = correlationCI(0.5, 103);
    expect(lo).toBeCloseTo(Math.tanh(Math.atanh(0.5) - 0.196), 12);
    expect(hi).toBeCloseTo(Math.tanh(Math.atanh(0.5) + 0.196), 12);
    expect(lo).toBeCloseTo(0.3393, 3);
    expect(hi).toBeCloseTo(0.6327, 3);
  });

  it("contains r, is asymmetric near the edge, and stays inside [-1, 1]", () => {
    const [lo, hi] = correlationCI(0.9, 30);
    expect(lo).toBeLessThan(0.9);
    expect(hi).toBeGreaterThan(0.9);
    expect(hi).toBeLessThanOrEqual(1);
    expect(0.9 - lo).toBeGreaterThan(hi - 0.9);
  });

  it("narrows as n grows", () => {
    const [a, b] = correlationCI(0.3, 20);
    const [c, d] = correlationCI(0.3, 2000);
    expect(d - c).toBeLessThan(b - a);
  });

  it("returns [-1, 1] when n <= 3 and stays finite for r = +/-1", () => {
    expect(correlationCI(0.5, 3)).toEqual([-1, 1]);
    const [lo, hi] = correlationCI(1, 50);
    expect(Number.isFinite(lo) && Number.isFinite(hi)).toBe(true);
  });
});

describe("crossCorrelation", () => {
  const peak = (xs: { lag: number; r: number }[]) => xs.reduce((best, c) => (c.r > best.r ? c : best));

  it("returns 2*maxLag+1 entries with shrinking n", () => {
    const out = crossCorrelation(noise(20), noise(20), 3);
    expect(out.map((o) => o.lag)).toEqual([-3, -2, -1, 0, 1, 2, 3]);
    expect(out.map((o) => o.n)).toEqual([17, 18, 19, 20, 19, 18, 17]);
  });

  it("finds the peak at +k when b is a copy of a delayed by k (a leads)", () => {
    const a = noise(60);
    const k = 2;
    const b = a.map((_, i) => (i - k >= 0 ? a[i - k] : 0));
    const out = crossCorrelation(a, b, 5);
    const best = peak(out);
    expect(best.lag).toBe(k);
    expect(best.r).toBeCloseTo(1, 10);
  });

  it("finds the peak at -k when b leads a", () => {
    const b = noise(60);
    const k = 3;
    const a = b.map((_, i) => (i - k >= 0 ? b[i - k] : 0));
    const best = peak(crossCorrelation(a, b, 5));
    expect(best.lag).toBe(-k);
  });

  it("gives r = 1 at lag 0 for identical series", () => {
    const a = noise(30);
    const zero = crossCorrelation(a, a, 2).find((o) => o.lag === 0)!;
    expect(zero.r).toBeCloseTo(1, 12);
  });

  it("is NaN-free for constant series and short series", () => {
    const flat = crossCorrelation([1, 1, 1, 1], [1, 2, 3, 4], 2);
    expect(flat.every((o) => Number.isFinite(o.r))).toBe(true);
    const short = crossCorrelation([1, 2], [2, 3], 5);
    expect(short.every((o) => Number.isFinite(o.r) && o.n >= 0)).toBe(true);
    expect(short).toHaveLength(11);
    expect(crossCorrelation([], [], 2).every((o) => o.r === 0 && o.n === 0)).toBe(true);
  });
});
