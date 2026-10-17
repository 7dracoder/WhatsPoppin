import { XAxis } from "recharts";
import { tickFormatterFor, timeTicks } from "../../../lib/chart/axes";
import { AXIS_TICK } from "./panelCommon";

/**
 * Numeric time axis with an explicit shared domain. Numeric (not category) so
 * points sit at their real time and evidence markers can use raw epoch ms.
 * Ticks are computed here (lib/chart/axes.ts) rather than left to recharts:
 * its numeric ticks land on arbitrary moments, while these fall on whole
 * hours or local midnights, so the labels line up with the day and week the
 * range buttons asked for. `interval={0}` draws every tick we hand over; the
 * count is already kept small.
 */
export function TimeAxis({ domain, show }: { domain: [number, number]; show: boolean }) {
  return (
    <XAxis
      dataKey="t"
      type="number"
      scale="time"
      domain={domain}
      ticks={timeTicks(domain)}
      interval={0}
      allowDataOverflow
      hide={!show}
      tickFormatter={tickFormatterFor(domain)}
      stroke="var(--text-tertiary)"
      tick={AXIS_TICK}
      axisLine={{ stroke: "var(--border)" }}
      tickLine={false}
    />
  );
}
