import type { NormalizedPoint, NormalizedSeries } from "../../types/market";

/** A series as it arrives from the API: irregular timestamps, real units. */
export interface RawPoint {
  t: number; // epoch ms
  value: number; // USD close, or odds 0-100
  volume?: number; // shares or contracts traded in this bar; absent when the venue has none
}

export type RawSeries = Omit<NormalizedSeries, "points"> & { points: RawPoint[] };

/**
 * Puts every series on one shared time grid and normalizes each to "change
 * since the start of the window". Pure (no React, no fetching) so it's easy to test.
 *
 * Window: the union of every series' span. A stock only has bars during
 * exchange sessions while a prediction market trades around the clock, so a
 * market's evening or weekend trades fall outside the stock's span; they are
 * still drawn, with the stock holding its last close through them. (It used
 * to be the stock's span alone, which made a one-day market that traded
 * after the close look like it had no trades at all.)
 *
 * Gaps are forward-filled, never interpolated: a stock holds its last close
 * through nights and weekends, and a prediction market's odds stay at the last
 * trade until the next one. Interpolating would invent prices that never existed.
 * A series has no point before its own first one: nothing is back-filled.
 * Volume is the exception: it belongs to one bar, so it is only carried on
 * the grid step that bar ends on and left undefined on filled steps (a
 * repeated close is still "no trade happened here").
 *
 * Normalization differs by unit on purpose:
 * - stock: % change from the first point.
 * - market odds: percentage-POINT change. Odds are already a percentage, so a
 *   % change misleads (3% -> 6% would read "+100%" for a 3-point move).
 */
export function alignAndNormalize(input: RawSeries[]): NormalizedSeries[] {
  const sorted = input.map((s) => ({ ...s, points: [...s.points].sort((a, b) => a.t - b.t) }));

  const grid = [...new Set(sorted.flatMap((s) => s.points.map((p) => p.t)))].sort((a, b) => a - b);
  if (grid.length === 0) return sorted.map((s) => ({ ...s, points: [] }));
  const start = grid[0];

  return sorted.map((s) => {
    let current: number | undefined;
    let next = 0;
    // Seed from the series' points at the window start (several at one instant: the last wins).
    while (next < s.points.length && s.points[next].t < start) {
      current = s.points[next].value;
      next += 1;
    }

    const points: NormalizedPoint[] = [];
    let baseline: number | undefined;
    for (const t of grid) {
      let volume: number | undefined; // only a bar ending exactly here has one
      while (next < s.points.length && s.points[next].t <= t) {
        current = s.points[next].value;
        volume = s.points[next].t === t ? s.points[next].volume : undefined;
        next += 1;
      }
      if (current === undefined) continue; // nothing known yet: leave a gap, don't invent a value
      baseline ??= current;
      const value =
        s.unit === "usd" ? (baseline === 0 ? 0 : ((current - baseline) / baseline) * 100) : current - baseline;
      points.push(volume === undefined ? { t, value, rawValue: current } : { t, value, rawValue: current, volume });
    }
    return { id: s.id, label: s.label, kind: s.kind, unit: s.unit, points };
  });
}
