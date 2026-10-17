import type { NormalizedPoint } from "../../types/market";
import { correlationCI, crossCorrelation, deltaLogit, logReturns, residualizeOnBenchmark, rollingCorrelation } from "./stats";

/**
 * Pure derivation for the relationship panel:
 * aligned grid values -> return arrays -> rolling r + CI -> lead-lag summary.
 * No React, no fetching, so every step is unit-testable.
 */

export const DEFAULT_WINDOW = 20; // ~20 observations: a common compromise between noise and responsiveness
export const DEFAULT_MAX_LAG = 6; // grid steps
export const MIN_PAIRS = 25; // below this a rolling r is mostly noise, so we refuse to show one

export interface AlignedLevels {
  t: number[];
  stock: number[];
  benchmark: number[] | null; // null = no benchmark available (stock is used as-is)
  odds: number[]; // 0-100
}

/**
 * Keep only grid times where every series has a value, so index i means the
 * same instant in every array. Series come from lib/chart/align.ts, which
 * already put them on one grid; here we just intersect by timestamp.
 *
 * LIMITATION: align.ts forward-fills, so an odds value that did not trade
 * between two grid steps is repeated and yields a Δlogit of exactly 0. Those
 * zeros are "no information", not "no change", and pull r toward 0 (similar in
 * spirit to the Epps effect, see stats.ts). We do NOT drop those steps: lags
 * are counted in grid steps, and deleting steps would make "k steps" mean
 * different durations. Instead we report `oddsActiveShare` (fraction of steps
 * where the odds actually moved) so the reader can see how thin the market is.
 */
export function alignLevels(
  stock: NormalizedPoint[],
  benchmark: NormalizedPoint[] | null,
  market: NormalizedPoint[],
): AlignedLevels {
  const bench = benchmark ? new Map(benchmark.map((p) => [p.t, p.rawValue])) : null;
  const odds = new Map(market.map((p) => [p.t, p.rawValue]));
  const out: AlignedLevels = { t: [], stock: [], benchmark: bench ? [] : null, odds: [] };
  for (const p of stock) {
    const o = odds.get(p.t);
    const b = bench?.get(p.t);
    if (o === undefined || (bench && b === undefined)) continue;
    out.t.push(p.t);
    out.stock.push(p.rawValue);
    out.benchmark?.push(b as number);
    out.odds.push(o);
  }
  return out;
}

export interface RollingPoint {
  t: number;
  r: number;
  lo: number;
  hi: number;
}

export type LeadLagKind = "odds-lead" | "stock-lead" | "together" | "none";

export interface LeadLag {
  kind: LeadLagKind;
  /** Steps by which odds lead the stock (negative: stock leads). 0 when kind is "none". */
  lag: number;
  r: number;
}

export interface RelationshipResult {
  /** "insufficient" = fewer than MIN_PAIRS aligned return pairs; no numbers are produced. */
  status: "ok" | "insufficient";
  pairs: number;
  rolling: RollingPoint[];
  latest: { r: number; n: number } | null;
  leadLag: LeadLag | null;
  /** Share of steps where the odds actually changed (low = thin market / mostly forward-filled). */
  oddsActiveShare: number;
}

export function computeRelationship(
  levels: AlignedLevels,
  window = DEFAULT_WINDOW,
  maxLag = DEFAULT_MAX_LAG,
): RelationshipResult {
  const stockRet = logReturns(levels.stock);
  const oddsChg = deltaLogit(levels.odds);
  const pairs = Math.min(stockRet.length, oddsChg.length);
  const empty: RelationshipResult = { status: "insufficient", pairs, rolling: [], latest: null, leadLag: null, oddsActiveShare: 0 };
  if (pairs < MIN_PAIRS) return empty;

  // Remove the market-wide move so we correlate with the stock-SPECIFIC return.
  // Beta is fit once on the full window (simple, stable); a rolling beta would be more adaptive but noisier.
  const stockAdj = levels.benchmark
    ? residualizeOnBenchmark(stockRet, logReturns(levels.benchmark)).residuals
    : stockRet;

  // A window longer than the data would never produce a point.
  const w = Math.min(window, pairs);
  const r = rollingCorrelation(stockAdj, oddsChg, w);
  const rolling: RollingPoint[] = [];
  r.forEach((value, i) => {
    if (value === null) return;
    // CI uses n = window: each r comes from exactly `w` observations.
    const [lo, hi] = correlationCI(value, w);
    // Return i spans grid steps i -> i+1, so it is known at time t[i+1].
    rolling.push({ t: levels.t[i + 1], r: value, lo, hi });
  });

  return {
    status: "ok",
    pairs,
    rolling,
    latest: rolling.length > 0 ? { r: rolling[rolling.length - 1].r, n: w } : null,
    leadLag: summarizeLeadLag(oddsChg, stockAdj, maxLag),
    oddsActiveShare: oddsChg.filter((d) => d !== 0).length / oddsChg.length,
  };
}

/**
 * Strongest cross-correlation across lags. Positive lag = odds lead the stock
 * (crossCorrelation(a=odds, b=stock) pairs odds_t with stock_{t+lag}).
 * We only call it a relationship if the best lag's 95% CI excludes 0.
 * Caveat: picking the max over 2*maxLag+1 lags is a multiple-comparisons
 * search, so this is exploratory and slightly optimistic.
 */
export function summarizeLeadLag(oddsChg: number[], stockAdj: number[], maxLag: number): LeadLag {
  const cc = crossCorrelation(oddsChg, stockAdj, maxLag);
  const best = cc.reduce((a, b) => (Math.abs(b.r) > Math.abs(a.r) ? b : a));
  const [lo, hi] = correlationCI(best.r, best.n);
  if (lo <= 0 && hi >= 0) return { kind: "none", lag: 0, r: best.r };
  if (best.lag === 0) return { kind: "together", lag: 0, r: best.r };
  return { kind: best.lag > 0 ? "odds-lead" : "stock-lead", lag: best.lag, r: best.r };
}

/** One plain-English sentence for the readout. */
export function describeLeadLag(ll: LeadLag): string {
  const r = ll.r.toFixed(2);
  const steps = (k: number) => `${Math.abs(k)} step${Math.abs(k) === 1 ? "" : "s"}`;
  switch (ll.kind) {
    case "odds-lead":
      return `odds lead the stock by ${steps(ll.lag)} (r=${r})`;
    case "stock-lead":
      return `the stock leads the odds by ${steps(ll.lag)} (r=${r})`;
    case "together":
      return `move together (r=${r})`;
    default:
      return "no clear relationship";
  }
}
