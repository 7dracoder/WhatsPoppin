/**
 * Time-zoom math for the overlay chart. Pure (no React, no recharts) so the
 * pixel -> time mapping and the clamping rules can be unit tested; the
 * component only wires pointer events to these.
 */
export type TimeDomain = [number, number];

/**
 * Narrowest zoom, as a fraction of the full window. A brush narrower than this
 * is treated as a click (nothing happens) rather than zooming into a sliver
 * with no points in it, and wheel zoom stops here.
 */
export const MIN_ZOOM_FRACTION = 0.01;

/** Points whose time falls inside the domain (inclusive). */
export function within<T extends { t: number }>(points: T[], domain: TimeDomain): T[] {
  return points.filter((p) => p.t >= domain[0] && p.t <= domain[1]);
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/**
 * The time under a pixel x, given the plot's left/right edges and the domain
 * drawn across them. The stacked panels share margins and a fixed y-axis
 * width (panelCommon.ts), so the same edges hold for every panel. Pixels
 * outside the plot clamp to its ends, so a drag that leaves the plot still
 * selects up to the edge.
 */
export function pixelToTime(x: number, plotLeft: number, plotRight: number, domain: TimeDomain): number {
  const width = plotRight - plotLeft;
  if (width <= 0) return domain[0];
  const fraction = clamp((x - plotLeft) / width, 0, 1);
  return domain[0] + fraction * (domain[1] - domain[0]);
}

/**
 * A brush between two times as a zoom domain: ordered, kept inside the full
 * window, and null when it is too narrow to be a deliberate selection.
 */
export function brushDomain(a: number, b: number, full: TimeDomain): TimeDomain | null {
  const lo = clamp(Math.min(a, b), full[0], full[1]);
  const hi = clamp(Math.max(a, b), full[0], full[1]);
  if (hi - lo < (full[1] - full[0]) * MIN_ZOOM_FRACTION) return null;
  return [lo, hi];
}

/**
 * Scale the current domain by `factor` (< 1 zooms in, > 1 out) keeping the
 * moment under the cursor (`anchor`) at the same screen position, the way a
 * map zooms. The result stays inside the full window and never narrower than
 * MIN_ZOOM_FRACTION of it; null means "back to the full window".
 */
export function zoomAround(current: TimeDomain, full: TimeDomain, anchor: number, factor: number): TimeDomain | null {
  const fullSpan = full[1] - full[0];
  const span = clamp((current[1] - current[0]) * factor, fullSpan * MIN_ZOOM_FRACTION, fullSpan);
  if (span >= fullSpan) return null;
  const t = clamp(anchor, current[0], current[1]);
  const fraction = (t - current[0]) / (current[1] - current[0]);
  let lo = t - fraction * span;
  // Slide the window back inside the data instead of shrinking it against an edge.
  lo = clamp(lo, full[0], full[1] - span);
  return [lo, lo + span];
}
