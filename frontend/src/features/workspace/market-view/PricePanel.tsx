import type { ReactNode } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceArea, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { formatVolume, volumeDomain } from "../../../lib/chart/axes";
import { within, type TimeDomain } from "../../../lib/chart/zoom";
import type { NormalizedSeries } from "../../../types/market";
import { PANEL_MARGIN, TIME_SYNC, TOOLTIP_STYLE, VOLUME_AXIS, VOLUME_BAR, Y_AXIS_WIDTH, formatTooltipDate } from "./panelCommon";
import { EdgeTick, type EdgeTickProps } from "./EdgeTick";
import { TimeAxis } from "./TimeAxis";
import { TIP_HEADROOM, TimelineAnnotations } from "./TimelineAnnotations";

interface PricePanelProps {
  stock: NormalizedSeries;
  domain: TimeDomain;
  /** Label the time axis: on when this is the bottom panel (no odds panel under it). */
  showTimeAxis?: boolean;
  color: string;
  /** A brush in progress (two times, any order), drawn as a translucent band. */
  selection?: TimeDomain | null;
  /** Extra chart children from OverlayChart's public `children` prop. */
  children?: ReactNode;
  /** Minimum pixel height; the panel grows past it when the chart has spare room. */
  height?: number;
}

const PriceTick = (props: EdgeTickProps) => <EdgeTick {...props} format={(v) => `$${v.toFixed(0)}`} liftBottom />;

/**
 * Whole-dollar y-axis whose first and last ticks sit exactly on the plot's bottom
 * and top edges. With recharts' own "auto" domain the lowest tick floats above
 * the bottom edge, leaving a blank strip that reads as a gap before the odds
 * panel; here the bottom gridline is the panel's floor, so the two panels meet.
 */
function priceAxis(values: number[]): { domainY: [number, number] | ["auto", "auto"]; ticksY: number[] | undefined } {
  if (values.length === 0) return { domainY: ["auto", "auto"], ticksY: undefined };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.05, 0.5);
  const lo = Math.floor(min - pad);
  const step = Math.max(1, Math.ceil((max + pad - lo) / 4));
  return { domainY: [lo, lo + 4 * step], ticksY: [0, 1, 2, 3, 4].map((i) => lo + i * step) };
}

/** Raw USD price (not rebased): the reader sees the real level; the relationship panel handles comparison. */
export function PricePanel({ stock, domain, color, selection, children, height = 180, showTimeAxis = false }: PricePanelProps) {
  const data = stock.points.map((p) => ({ t: p.t, price: p.rawValue, volume: p.volume }));
  // Both y-axes fit what is on screen: zoomed into a quiet hour, the line should fill the
  // panel, not sit flat at the scale of the whole week. allowDataOverflow below keeps
  // recharts from stretching the axes back out to the off-screen points.
  const visible = within(data, domain);
  const { domainY, ticksY } = priceAxis(visible.map((d) => d.price));
  const hasVolume = visible.some((d) => d.volume !== undefined);
  return (
    <div aria-label={`${stock.label} price`} className="w-full" style={{ flex: `5 1 ${height}px`, minHeight: height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ ...PANEL_MARGIN, top: TIP_HEADROOM }} {...TIME_SYNC}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <TimeAxis domain={domain} show={showTimeAxis} />
          <YAxis
            width={Y_AXIS_WIDTH}
            domain={domainY}
            ticks={ticksY}
            tick={PriceTick}
            axisLine={false}
            tickLine={false}
            allowDataOverflow
          />
          {/* Hidden axis for the volume strip: its own scale, so bars stay in the bottom quarter. */}
          <YAxis {...VOLUME_AXIS} domain={volumeDomain(visible.map((d) => d.volume))} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelFormatter={(t) => formatTooltipDate(Number(t))}
            formatter={(v, key) =>
              key === "volume" ? [`${formatVolume(Number(v))} shares`, "Volume"] : [`$${Number(v).toFixed(2)}`, stock.label]
            }
          />
          {hasVolume && (
            <Bar dataKey="volume" name="volume" yAxisId={VOLUME_AXIS.yAxisId} fill="var(--text-tertiary)" {...VOLUME_BAR} />
          )}
          {/* Monotone cubic: smooth but never overshoots a bar's value. */}
          <Line
            type="monotone"
            dataKey="price"
            stroke={color}
            strokeWidth={2.5}
            dot={false}
            isAnimationActive={false}
          />
          <TimelineAnnotations domain={domain} />
          {selection && <ReferenceArea x1={selection[0]} x2={selection[1]} fill="var(--accent)" fillOpacity={0.12} />}
          {children}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
