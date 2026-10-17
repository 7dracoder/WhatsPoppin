/**
 * Every stacked panel uses the SAME margins and a fixed y-axis width, so the
 * plot areas have identical left/right edges and one x position means one
 * moment in time across all panels.
 */
export const PANEL_MARGIN = { top: 8, right: 16, left: 0, bottom: 0 };
export const Y_AXIS_WIDTH = 56;

export const AXIS_TICK = { fill: "var(--text-tertiary)", fontSize: 12 };

export const TOOLTIP_STYLE = {
  background: "var(--surface-elevated)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-control)",
  color: "var(--text-primary)",
  fontSize: 12,
};

export function formatTick(t: number): string {
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function formatTooltipDate(t: number): string {
  return new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/**
 * Charts sharing this id share their hover: pointing at a moment in one panel
 * draws the cursor and tooltip at that same moment in the other. "value" matches
 * on the x value (epoch ms) rather than the data index, which is required here
 * because the two panels have different numbers of points.
 */
export const TIME_SYNC = { syncId: "price-odds-time", syncMethod: "value" as const };

/**
 * The volume strip's own y-axis, hidden: its scale is set by lib/chart/axes.ts
 * volumeDomain so bars stay in the bottom quarter of the panel, under the line.
 */
export const VOLUME_AXIS = { yAxisId: "volume", orientation: "right" as const, hide: true };

/** Muted bars (a trading-chart convention) that don't light up on hover: the line is the subject, volume the context. */
export const VOLUME_BAR = { fillOpacity: 0.3, isAnimationActive: false, activeBar: false as const, maxBarSize: 14 };
