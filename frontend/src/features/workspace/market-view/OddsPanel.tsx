import { Bar, CartesianGrid, ComposedChart, Line, ReferenceArea, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { fitOddsDomain, formatVolume, volumeDomain } from "../../../lib/chart/axes";
import { within, type TimeDomain } from "../../../lib/chart/zoom";
import type { NormalizedSeries } from "../../../types/market";
import { PANEL_MARGIN, TIME_SYNC, TOOLTIP_STYLE, VOLUME_AXIS, VOLUME_BAR, Y_AXIS_WIDTH, formatTooltipDate } from "./panelCommon";
import { EdgeTick, type EdgeTickProps } from "./EdgeTick";
import { TimeAxis } from "./TimeAxis";
import { TimelineAnnotations } from "./TimelineAnnotations";

interface OddsPanelProps {
  markets: NormalizedSeries[];
  domain: TimeDomain;
  colorById: Map<string, string>;
  /** A brush in progress (two times, any order), drawn as a translucent band. */
  selection?: TimeDomain | null;
  /** Show date labels here when no panel below carries them. */
  showTimeAxis?: boolean;
  /** Minimum pixel height; the panel grows past it when the chart has spare room. */
  height?: number;
  /** Fit the y-axis to the data on screen (lib/chart/axes.ts) instead of the full 0-100%. */
  fit?: boolean;
}

const OddsTick = (props: EdgeTickProps) => <EdgeTick {...props} format={(v) => `${v}%`} hangTop />;

/** Row key for a market's volume bar: the id is the odds line, so the bar needs its own. */
const volumeKey = (id: string) => `${id}\u0000volume`;

/** Real percent axis (fitted to the data by default): a 3-point move reads as 3 points, not an amplified rescale. */
export function OddsPanel({
  markets,
  domain,
  colorById,
  selection,
  showTimeAxis = false,
  height = 190,
  fit = true,
}: OddsPanelProps) {
  // Markets share one time grid (lib/chart/align.ts), so rows can be keyed by timestamp.
  const rows = new Map<number, Record<string, number>>();
  for (const m of markets) {
    for (const p of m.points) {
      const row = { ...rows.get(p.t), t: p.t, [m.id]: p.rawValue };
      if (p.volume !== undefined) row[volumeKey(m.id)] = p.volume;
      rows.set(p.t, row);
    }
  }
  const data = [...rows.values()].sort((a, b) => a.t - b.t);
  const labelById = new Map(markets.map((m) => [m.id, m.label]));
  const visible = markets.map((m) => within(m.points, domain));
  // Only venues with a volume feed (Kalshi) get bars; Polymarket's history has none.
  const withVolume = markets.filter((_, i) => visible[i].some((p) => p.volume !== undefined));
  const oddsDomain: [number, number] = fit ? fitOddsDomain(visible.flatMap((pts) => pts.map((p) => p.rawValue))) : [0, 100];

  return (
    <div aria-label="Market odds" className="relative w-full" style={{ flex: `4 1 ${height}px`, minHeight: height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ ...PANEL_MARGIN, top: 0 }} {...TIME_SYNC}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <TimeAxis domain={domain} show={showTimeAxis} />
          <YAxis
            width={Y_AXIS_WIDTH}
            domain={oddsDomain}
            ticks={fit ? undefined : [0, 25, 50, 75, 100]}
            tickFormatter={(v: number) => `${v}%`}
            tick={OddsTick}
            axisLine={false}
            tickLine={false}
            allowDataOverflow
          />
          <YAxis {...VOLUME_AXIS} domain={volumeDomain(visible.flat().map((p) => p.volume))} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelFormatter={(t) => formatTooltipDate(Number(t))}
            formatter={(v, key) => {
              const k = String(key);
              const volumeOf = withVolume.find((m) => volumeKey(m.id) === k);
              if (volumeOf) return [`${formatVolume(Number(v))} contracts`, `${volumeOf.label} volume`];
              return [`${Number(v).toFixed(1)}%`, labelById.get(k) ?? k];
            }}
          />
          {/* Evidence lines continue down from the price panel; its tags stay up there. */}
          <TimelineAnnotations domain={domain} variant="lines" />
          {withVolume.map((m) => (
            // Stacked so two markets' bars share the strip instead of hiding each other.
            <Bar
              key={volumeKey(m.id)}
              dataKey={volumeKey(m.id)}
              stackId="volume"
              yAxisId={VOLUME_AXIS.yAxisId}
              fill={colorById.get(m.id)}
              {...VOLUME_BAR}
            />
          ))}
          {markets.map((m) => (
            // stepAfter: a market's price IS its last trade, so a curve would imply prices that never traded.
            <Line
              key={m.id}
              type="stepAfter"
              dataKey={m.id}
              stroke={colorById.get(m.id)}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          ))}
          {selection && <ReferenceArea x1={selection[0]} x2={selection[1]} fill="var(--accent)" fillOpacity={0.12} />}
        </ComposedChart>
      </ResponsiveContainer>
      {withVolume.length === 0 && markets.length > 0 && (
        <span
          className="pointer-events-none absolute bottom-7 left-16 text-[11px]"
          style={{ color: "var(--text-tertiary)" }}
        >
          Volume unavailable for this venue
        </span>
      )}
    </div>
  );
}
