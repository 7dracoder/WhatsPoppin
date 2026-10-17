import { describe, expect, it } from "vitest";
import type { NormalizedPoint } from "../../types/market";
import { alignLevels, computeRelationship, describeLeadLag, MIN_PAIRS, type AlignedLevels } from "./relationship";

const pts = (vals: number[], start = 0): NormalizedPoint[] =>
  vals.map((v, i) => ({ t: (start + i) * 1000, value: 0, rawValue: v }));

/** Deterministic noise in [-1, 1]. */
const noise = (i: number) => Math.sin(i * 12.9898) * 2 - Math.sin(i * 78.233);

/** Build levels from per-step shocks: stock log-return = shock, odds logit change = lagged shock. */
function levelsFromShocks(n: number, oddsLeadsBy: number, inverse = false): AlignedLevels {
  const shocks = Array.from({ length: n }, (_, i) => noise(i) * 0.01);
  const stock = [100];
  const logitOdds = [0];
  for (let i = 1; i < n; i += 1) {
    stock.push(stock[i - 1] * Math.exp(shocks[i]));
    // odds at i react to the shock `oddsLeadsBy` steps LATER in the stock => odds lead the stock
    const src = shocks[Math.max(0, Math.min(n - 1, i + oddsLeadsBy))];
    logitOdds.push(logitOdds[i - 1] + (inverse ? -src : src) * 5);
  }
  const odds = logitOdds.map((l) => 100 / (1 + Math.exp(-l)));
  return { t: stock.map((_, i) => i * 1000), stock, benchmark: null, odds };
}

describe("alignLevels", () => {
  it("keeps only timestamps present in every series", () => {
    const out = alignLevels(pts([1, 2, 3, 4]), pts([10, 20, 30, 40]), pts([5, 6], 2));
    expect(out.t).toEqual([2000, 3000]);
    expect(out.stock).toEqual([3, 4]);
    expect(out.benchmark).toEqual([30, 40]);
    expect(out.odds).toEqual([5, 6]);
  });

  it("works without a benchmark", () => {
    expect(alignLevels(pts([1, 2]), null, pts([5, 6])).benchmark).toBeNull();
  });
});

describe("computeRelationship", () => {
  it("refuses to produce numbers with too few pairs", () => {
    const res = computeRelationship(levelsFromShocks(MIN_PAIRS - 1, 0)); // n-1 pairs < MIN_PAIRS
    expect(res.status).toBe("insufficient");
    expect(res.rolling).toEqual([]);
    expect(res.latest).toBeNull();
  });

  it("detects contemporaneous co-movement", () => {
    const res = computeRelationship(levelsFromShocks(80, 0));
    expect(res.status).toBe("ok");
    expect(res.leadLag?.kind).toBe("together");
    expect(res.latest?.r).toBeGreaterThan(0.9);
    expect(res.rolling.every((p) => p.lo <= p.r + 1e-3 && p.r - 1e-3 <= p.hi)).toBe(true);
    expect(res.rolling.every((p) => p.r >= -1 && p.r <= 1)).toBe(true);
  });

  it("detects odds leading the stock", () => {
    const res = computeRelationship(levelsFromShocks(120, 2));
    expect(res.leadLag?.kind).toBe("odds-lead");
    expect(res.leadLag?.lag).toBe(2);
  });

  it("detects the stock leading the odds (negative lag)", () => {
    const res = computeRelationship(levelsFromShocks(120, -2));
    expect(res.leadLag?.kind).toBe("stock-lead");
    expect(res.leadLag?.lag).toBe(-2);
  });

  it("reports a negative relationship", () => {
    const res = computeRelationship(levelsFromShocks(80, 0, true));
    expect(res.latest?.r).toBeLessThan(-0.9);
  });

  it("reports no clear relationship for unrelated series", () => {
    const base = levelsFromShocks(80, 0);
    const odds = base.odds.map((_, i) => 50 + 10 * Math.sin(i * 7.31 + Math.cos(i * 3.7)));
    const res = computeRelationship({ ...base, odds });
    expect(res.leadLag?.kind).toBe("none");
  });

  it("removes the benchmark move: a stock that is pure SPY has no residual signal", () => {
    const base = levelsFromShocks(80, 0);
    // stock == 2x SPY exactly; odds track the stock. After removing SPY nothing is left.
    const res = computeRelationship({ ...base, benchmark: base.stock.map((p) => 100 * Math.sqrt(p / 100)) });
    expect(res.status).toBe("ok");
    expect(res.leadLag?.kind).toBe("none");
  });

  it("measures how often the odds actually moved (forward-fill thinness)", () => {
    const base = levelsFromShocks(60, 0);
    const odds = base.odds.map((o, i) => (i % 2 === 0 ? o : base.odds[i - 1])); // every other step is a repeat
    const res = computeRelationship({ ...base, odds });
    expect(res.oddsActiveShare).toBeGreaterThan(0.4);
    expect(res.oddsActiveShare).toBeLessThan(0.6);
  });
});

describe("describeLeadLag", () => {
  it("writes plain English", () => {
    expect(describeLeadLag({ kind: "odds-lead", lag: 1, r: 0.456 })).toBe("odds lead the stock by 1 step (r=0.46)");
    expect(describeLeadLag({ kind: "stock-lead", lag: -3, r: -0.3 })).toBe("the stock leads the odds by 3 steps (r=-0.30)");
    expect(describeLeadLag({ kind: "together", lag: 0, r: 0.5 })).toBe("move together (r=0.50)");
    expect(describeLeadLag({ kind: "none", lag: 0, r: 0.1 })).toBe("no clear relationship");
  });
});
