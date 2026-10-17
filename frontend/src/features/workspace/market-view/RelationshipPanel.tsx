import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { describeLeadLag, type RelationshipResult } from "../../../lib/chart/relationship";
import type { NormalizedSeries } from "../../../types/market";
import { AXIS_TICK, PANEL_MARGIN, TOOLTIP_STYLE, Y_AXIS_WIDTH, formatTooltipDate } from "./panelCommon";
import { TimeAxis } from "./TimeAxis";
import { TIP_HEADROOM, TimelineAnnotations } from "./TimelineAnnotations";

interface RelationshipPanelProps {
  result: RelationshipResult | null; // null = no market selected
  /** How the correlation was sampled when that differs from the displayed series (a young market). */
  sampling?: string | null;
  /** Why there aren't enough pairs, when there aren't. */
  detail?: string | null;
  markets: NormalizedSeries[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  domain: [number, number];
  color: string;
  chartHeight?: number;
}

export function RelationshipPanel({
  result,
  sampling = null,
  detail = null,
  markets,
  selectedId,
  onSelect,
  domain,
  color,
  chartHeight = 170,
}: RelationshipPanelProps) {
  const muted = { color: "var(--text-tertiary)" };

  return (
    <section aria-label="Stock and odds relationship" className="flex flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2 px-1">
        <h4 className="text-xs font-medium uppercase tracking-wide" style={muted}>
          Rolling correlation (SPY-adjusted stock return vs odds change)
        </h4>
        {markets.length > 1 && (
          <div role="radiogroup" aria-label="Market used for correlation" className="flex flex-wrap gap-3 text-xs">
            {markets.map((m) => (
              <label
                key={m.id}
                className="flex cursor-pointer items-center gap-1"
                style={{ color: "var(--text-secondary)" }}
              >
                <input
                  type="radio"
                  name="relationship-market"
                  checked={m.id === selectedId}
                  onChange={() => onSelect(m.id)}
                />
                {m.label}
              </label>
            ))}
          </div>
        )}
      </div>

      {result === null ? (
        <p className="px-1 text-sm" style={muted}>
          Select a market to see how it relates to the stock.
        </p>
      ) : result.status === "insufficient" ? (
        <p className="px-1 text-sm" style={muted}>
          Not enough overlapping data{detail ? `: ${detail}` : "."}
        </p>
      ) : (
        <>
          {sampling && (
            <p className="px-1 text-xs" style={muted}>
              {sampling}
            </p>
          )}
          <div
            aria-label="Rolling correlation chart"
            className="w-full"
            style={{ flex: `1 1 ${chartHeight}px`, minHeight: chartHeight }}
          >
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart
                data={result.rolling.map((p) => ({ t: p.t, r: p.r, band: [p.lo, p.hi] }))}
                margin={{ ...PANEL_MARGIN, top: TIP_HEADROOM, bottom: 4 }}
              >
                <CartesianGrid stroke="var(--border)" vertical={false} />
                <TimeAxis domain={domain} show />
                {/* Fixed [-1, 1]: auto-scaling would make a weak r look dramatic. */}
                <YAxis
                  width={Y_AXIS_WIDTH}
                  domain={[-1, 1]}
                  ticks={[-1, -0.5, 0, 0.5, 1]}
                  tick={AXIS_TICK}
                  axisLine={false}
                  tickLine={false}
                />
                <ReferenceLine y={0} stroke="var(--text-tertiary)" strokeDasharray="4 4" />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelFormatter={(t) => formatTooltipDate(Number(t))}
                  formatter={(v, key) =>
                    key === "band" && Array.isArray(v)
                      ? [`${Number(v[0]).toFixed(2)} to ${Number(v[1]).toFixed(2)}`, "95% CI"]
                      : [Number(v).toFixed(2), "r"]
                  }
                />
                {/* The same evidence tags and dotted lines as the price chart, so a move in r can be read against the news. */}
                <TimelineAnnotations domain={domain} />
                <Area
                  type="monotone"
                  dataKey="band"
                  stroke="none"
                  fill={color}
                  fillOpacity={0.18}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="r"
                  stroke={color}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="px-1 text-sm" style={{ color: "var(--text-secondary)" }}>
            {result.latest
              ? `Latest r = ${result.latest.r.toFixed(2)} (n = ${result.latest.n})`
              : "Latest r unavailable"}
            {result.leadLag && ` · Best lead-lag: ${describeLeadLag(result.leadLag)}`}
            {` · odds moved in ${Math.round(result.oddsActiveShare * 100)}% of steps`}
          </p>
          <p className="px-1 text-xs" style={muted}>
            Association, not causation; thin markets inflate noise.
          </p>
        </>
      )}
    </section>
  );
}
