import { ArrowLeft, ArrowSquareOut, ArrowsOutLineHorizontal, MagnifyingGlassMinus } from "@phosphor-icons/react";
import { useContext, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { describeAge, fetchSeriesBundle, type MarketMeta, type SeriesBundle } from "../../../lib/api/series";
import { alignLevels, computeRelationship, MIN_PAIRS, type RelationshipResult } from "../../../lib/chart/relationship";
import { brushDomain, pixelToTime, zoomAround, type TimeDomain } from "../../../lib/chart/zoom";
import type { MarketCard, NormalizedSeries } from "../../../types/market";
import { ThemeContext } from "../../theme/ThemeProvider";
import { useWorkspace } from "../useWorkspace";
import { OddsPanel } from "./OddsPanel";
import { OutcomePicker } from "./OutcomePicker";
import { PANEL_MARGIN, Y_AXIS_WIDTH, formatTooltipDate } from "./panelCommon";
import { PricePanel } from "./PricePanel";
import { DayStepper, RangeSelector } from "./RangeSelector";
import { RelationshipPanel } from "./RelationshipPanel";

/**
 * Categorical "series identity" palette — intentionally separate from both
 * the theme's `--accent` and the locked `--status-*` tokens (see CLAUDE
 * instructions passed down from the design spec). Fixed order, never
 * cycled/reassigned when the series set changes, so a market's color is
 * stable across re-renders. Light/dark steps per the dataviz skill's
 * validated categorical table (blue / orange / aqua / yellow), each pair
 * checked with validate_palette.js against this app's chart surfaces.
 */
const SERIES_PALETTE: Array<{ light: string; dark: string }> = [
  { light: "#2a78d6", dark: "#3987e5" }, // blue
  { light: "#eb6834", dark: "#d95926" }, // orange
  { light: "#1baf7a", dark: "#199e70" }, // aqua
  { light: "#eda100", dark: "#c98500" }, // yellow
];
const FALLBACK_SERIES_COLOR = { light: "#8a8f98", dark: "#a7acb6" };

function seriesColor(marketIndex: number, mode: "light" | "dark"): string {
  const step = SERIES_PALETTE[marketIndex] ?? FALLBACK_SERIES_COLOR;
  return mode === "dark" ? step.dark : step.light;
}

/** Skeleton shaped like the eventual chart area — axes + a faint wandering line — not a spinner. */
function ChartSkeleton() {
  return (
    <div
      className="flex h-full min-h-[280px] w-full flex-col gap-3"
      role="status"
      aria-label="Loading comparison chart"
    >
      <div className="flex items-center gap-3">
        <div className="h-3 w-24 animate-pulse rounded-full" style={{ background: "var(--border)" }} />
        <div className="h-3 w-16 animate-pulse rounded-full" style={{ background: "var(--border)" }} />
      </div>
      <div
        className="relative flex-1 overflow-hidden rounded-[var(--radius-control)]"
        style={{ background: "var(--surface-elevated)" }}
      >
        <svg viewBox="0 0 400 160" preserveAspectRatio="none" className="h-full w-full">
          {[20, 60, 100, 140].map((y) => (
            <line key={y} x1="0" x2="400" y1={y} y2={y} stroke="var(--border)" strokeWidth="1" />
          ))}
          <path
            d="M0,120 C40,100 60,60 100,70 C140,80 160,40 200,50 C240,60 260,110 300,90 C340,70 360,30 400,45"
            fill="none"
            stroke="var(--text-tertiary)"
            strokeWidth="2"
            className="animate-pulse"
          />
        </svg>
      </div>
      <div className="flex gap-4">
        <div className="h-2 w-10 animate-pulse rounded-full" style={{ background: "var(--border)" }} />
        <div className="h-2 w-14 animate-pulse rounded-full" style={{ background: "var(--border)" }} />
      </div>
    </div>
  );
}

interface LegendEntry {
  key: string;
  label: string;
  kind: NormalizedSeries["kind"];
  color: string;
  /** Markets only: the answer whose odds the line is, the market's own title and rule, and its page. */
  outcome?: string | null;
  question?: string | null;
  rules?: string | null;
  url?: string | null;
  /** Markets only: the card, for the outcome picker. */
  card?: MarketCard;
}

/**
 * One row per series. A market's row names the event's question AND the
 * outcome, because an event like "What will Jensen Huang say?" has one market
 * per answer and the line is the odds of one of them. The venue's title and
 * resolution rule sit in the hover text, and the link opens the market's page.
 */
function ChartLegend({ entries }: { entries: LegendEntry[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-5 gap-y-1 px-1" aria-label="Chart series legend">
      {entries.map((entry) => {
        const hover = [entry.question, entry.rules].filter(Boolean).join("\n\n") || undefined;
        return (
          <li key={entry.key} className="flex items-center gap-2 text-sm" style={{ color: "var(--text-secondary)" }}>
            <span aria-hidden className="inline-block h-[3px] w-4 rounded-full" style={{ background: entry.color }} />
            <span title={hover}>
              {entry.label}
              {entry.outcome && (
                <>
                  {" "}
                  <span style={{ color: "var(--text-tertiary)" }}>·</span>{" "}
                  <span className="font-semibold" style={{ color: "var(--text-primary)" }}>
                    {entry.outcome}
                  </span>
                </>
              )}
            </span>
            <span className="text-xs uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>
              {entry.kind === "stock" ? "stock" : entry.outcome ? "odds of this outcome" : "market odds"}
            </span>
            {entry.card && <OutcomePicker card={entry.card} />}
            {entry.url && (
              <a
                href={entry.url}
                target="_blank"
                rel="noreferrer"
                aria-label={`Open ${entry.label} on its venue`}
                title="Open on the venue"
                className="flex items-center"
                style={{ color: "var(--text-tertiary)" }}
              >
                <ArrowSquareOut size={14} weight="bold" />
              </a>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export interface OverlayChartProps {
  /** Overridden mostly for tests/storybook-style use — defaults to the workspace's active ticker. */
  ticker?: string;
  /** Overridden mostly for tests/storybook-style use — defaults to the workspace's selected markets. */
  marketIds?: string[];
  /**
   * Extension point for layering on timeline annotation lines / click targets
   * (Stage 7-8, built elsewhere) without this chart becoming a sealed box.
   * Children are rendered as additional children of the price panel's recharts <LineChart>,
   * so a caller can drop in <ReferenceLine>/<ReferenceDot> etc. positioned
   * against the price panel's x/y scale (x is epoch ms). Evidence markers
   * are already rendered by the chart itself.
   */
  children?: ReactNode;
  className?: string;
}

/**
 * Two views, switched by a tab: "Price & odds" stacks price (USD) over odds
 * (real 0-100%, optionally zoomed) on one time axis; "Correlation" shows the
 * rolling correlation between SPY-adjusted stock returns and odds changes.
 * Components are thin; all math lives in lib/chart/relationship.ts.
 */
/**
 * One wheel notch zooms the window by this factor (in or out). Ctrl/Cmd is
 * required: a bare wheel still scrolls the page, as it does everywhere else.
 */
const WHEEL_ZOOM_STEP = 1.2;

interface Relationship {
  result: RelationshipResult;
  /** Shown over the panel when the correlation was sampled differently from the displayed series. */
  sampling: string | null;
  /** Why there aren't enough pairs, when there aren't. */
  detail: string | null;
}

/**
 * The relationship for one market: from the bundle's finer-sampled series
 * when the loader made one (a young market), otherwise from what's displayed.
 * If the fine sampling has too few pairs (the market's last session is older
 * than the 1D window) the displayed series still get a chance.
 */
function relationshipFor(
  stock: NormalizedSeries,
  bundle: SeriesBundle,
  market: NormalizedSeries,
  now: number,
): Relationship {
  const fine = bundle.correlation[market.id];
  const candidates: Array<{ result: RelationshipResult; sampling: string | null; interval: string | null }> = [];
  if (fine) {
    candidates.push({
      result: computeRelationship(alignLevels(fine.stock, fine.benchmark, fine.market)),
      sampling: fine.reason,
      interval: fine.interval,
    });
  }
  candidates.push({
    result: computeRelationship(alignLevels(stock.points, bundle.benchmark?.points ?? null, market.points)),
    sampling: null,
    interval: bundle.interval,
  });
  const best =
    candidates.find((c) => c.result.status === "ok") ??
    candidates.reduce((a, b) => (b.result.pairs > a.result.pairs ? b : a));
  return {
    result: best.result,
    sampling: best.result.status === "ok" ? best.sampling : null,
    detail:
      best.result.status === "ok"
        ? null
        : describeInsufficient(best.result, best.interval, bundle.markets[market.id], now),
  };
}

/** Why a market has too few return pairs: its age, and what sampling would be enough. */
function describeInsufficient(
  result: RelationshipResult,
  interval: string | null,
  meta: MarketMeta | undefined,
  now: number,
): string {
  const step = interval ? ` at ${interval} steps` : "";
  const age = meta?.openTime != null ? ` This market opened ${describeAge(now - meta.openTime)} ago.` : "";
  const need =
    interval === "1m"
      ? " Its last session had too few trades alongside stock bars; try a day it traded on."
      : ` ${MIN_PAIRS} pairs need about a week of hourly bars, or one session at the 1D range.`;
  return `${result.pairs} of ${MIN_PAIRS} needed return pairs${step}.${age}${need}`;
}

/** Why a market series is empty, as honestly as the loader's metadata allows. */
function emptyNote(s: NormalizedSeries, meta: MarketMeta | undefined, domain: [number, number] | null): string {
  if (s.kind !== "market") return "No data in this window";
  const open = meta?.openTime;
  if (open == null) return "No trades in this window";
  if (domain && open > domain[1]) return `Opens ${formatTooltipDate(open)}, after this window`;
  return `No trades in this window (open since ${formatTooltipDate(open)})`;
}

export function OverlayChart({ ticker, marketIds, children, className }: OverlayChartProps) {
  const { state, dispatch } = useWorkspace();
  const themeCtx = useContext(ThemeContext);
  const mode = themeCtx?.mode ?? "light";

  const effectiveTicker = ticker ?? state.ticker ?? "";
  const effectiveMarketIds = marketIds ?? state.selectedMarketIds;
  const range = state.range;
  const chartDay = state.chartDay;

  const [bundle, setBundle] = useState<SeriesBundle | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "error" | "done">("idle");
  const [relationshipPick, setRelationshipPick] = useState<string | null>(null);
  // One chart view at a time: stacking all three made it hard to tell which
  // panel a line belonged to. "main" is price + odds on a shared time axis;
  // "correlation" is the rolling-correlation analysis.
  const [view, setView] = useState<"main" | "correlation">("main");
  // Fitted by default: most markets move a few points, invisible on a 0-100 axis. The toggle
  // flips to the full scale when the reader wants the move in proportion to certainty.
  const [fitOdds, setFitOdds] = useState(true);
  // Time zoom is view state, not workspace state: it is never saved with the project and
  // resets whenever the data changes (new range, day, ticker or markets).
  const [zoom, setZoom] = useState<TimeDomain | null>(null);
  const [selection, setSelection] = useState<TimeDomain | null>(null);

  useEffect(() => {
    if (!effectiveTicker) {
      setBundle(null);
      setStatus("idle");
      return;
    }
    let cancelled = false;
    setStatus("loading");
    fetchSeriesBundle(
      effectiveTicker,
      effectiveMarketIds,
      range,
      Object.fromEntries(state.suggestedMarkets.map((m) => [m.marketId, m.title])),
      chartDay,
    )
      .then((result) => {
        if (cancelled) return;
        setBundle(result);
        setStatus("done");
      })
      .catch(() => {
        if (cancelled) return;
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
    // effectiveMarketIds is derived fresh each render from an array dep — stringify to avoid refetch loops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveTicker, JSON.stringify(effectiveMarketIds), range, chartDay]);

  const series = bundle?.series ?? null;
  const stock = series?.find((s) => s.kind === "stock") ?? null;
  const markets = useMemo(() => (series ?? []).filter((s) => s.kind === "market"), [series]);

  // Shared x-domain for every panel: the window the range asked for (1D is a day, 1W a
  // week, ending where the backend's window ended), so the axis agrees with the range and
  // day controls even where the stock has no bars. The data's own span is the fallback for
  // the legacy routes, which don't report their window.
  const fullDomain = useMemo<TimeDomain | null>(() => {
    if (bundle?.window) return bundle.window;
    const pts = stock?.points ?? [];
    return pts.length > 0 ? [pts[0].t, pts[pts.length - 1].t] : null;
  }, [bundle, stock]);
  useEffect(() => {
    setZoom(null);
    setSelection(null);
  }, [bundle]);
  // What every panel draws: the zoomed span when there is one, else the whole window.
  const domain = zoom ?? fullDomain;

  const cardsById = useMemo(
    () => new Map(state.suggestedMarkets.map((m) => [m.marketId, m])),
    [state.suggestedMarkets],
  );
  const legendEntries = useMemo<LegendEntry[]>(() => {
    if (!series) return [];
    let marketIndex = 0;
    return series.map((s) => {
      if (s.kind === "stock") {
        return { key: s.id, label: s.label, kind: s.kind, color: "var(--text-primary)" };
      }
      const color = seriesColor(marketIndex, mode);
      marketIndex += 1;
      const card = cardsById.get(s.id);
      return {
        key: s.id,
        label: s.label,
        kind: s.kind,
        color,
        outcome: card?.outcome,
        question: card?.question,
        rules: card?.rules,
        url: card?.url,
        card,
      };
    });
  }, [series, mode, cardsById]);

  const colorById = useMemo(() => new Map(legendEntries.map((e) => [e.key, e.color])), [legendEntries]);

  // One relationship per market, so the default pick can be the first market
  // that actually has enough data rather than the first in the list.
  const relationships = useMemo(() => {
    const now = Date.now();
    return new Map(stock && bundle ? markets.map((m) => [m.id, relationshipFor(stock, bundle, m, now)] as const) : []);
  }, [stock, bundle, markets]);
  const relationshipMarket =
    markets.find((m) => m.id === relationshipPick) ??
    markets.find((m) => relationships.get(m.id)?.result.status === "ok") ??
    markets[0] ??
    null;
  const relationship = relationshipMarket ? (relationships.get(relationshipMarket.id) ?? null) : null;

  // Horizontal drag is a brush: it selects a time span and zooms every panel to it.
  // It used to step the window a day per 120px instead. The two can't share a plain
  // drag, and the brush won it because it is the trading-chart convention (TradingView,
  // Yahoo) and day-stepping already has its own buttons in the DayStepper, while a
  // zoom had no other way in. A modifier for one of them was rejected: undiscoverable,
  // and the buttons already cover stepping. The time under the pointer comes from the
  // plot geometry every panel shares (same margins, same y-axis width), so one drag
  // across the stack reads the same on each panel; the selection is drawn by each
  // panel through recharts' own scale, which is the check that the two agree.
  const plotRef = useRef<HTMLDivElement>(null);
  const brush = useRef<{ start: number; pointerId: number } | null>(null);
  function timeAt(clientX: number): number | null {
    const el = plotRef.current;
    if (!el || !domain) return null;
    const rect = el.getBoundingClientRect();
    const left = rect.left + PANEL_MARGIN.left + Y_AXIS_WIDTH;
    const right = rect.right - PANEL_MARGIN.right;
    return pixelToTime(clientX, left, right, domain);
  }
  function onBrushStart(e: ReactPointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    const t = timeAt(e.clientX);
    if (t === null) return;
    brush.current = { start: t, pointerId: e.pointerId };
    // Capture so the brush finishes on pointer-up even when the pointer leaves the chart.
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function onBrushMove(e: ReactPointerEvent<HTMLDivElement>) {
    const b = brush.current;
    if (!b) return;
    const t = timeAt(e.clientX);
    if (t !== null) setSelection([b.start, t]);
  }
  function onBrushEnd(e: ReactPointerEvent<HTMLDivElement>) {
    const b = brush.current;
    brush.current = null;
    setSelection(null);
    if (!b || !fullDomain) return;
    const t = timeAt(e.clientX);
    if (t === null) return;
    const next = brushDomain(b.start, t, fullDomain);
    if (next) setZoom(next); // a tiny drag is a click: leave the zoom alone
  }
  function onBrushCancel() {
    brush.current = null;
    setSelection(null);
  }
  // Ctrl/Cmd + wheel zooms around the cursor. A native listener because React registers
  // wheel as passive, and preventDefault is needed to stop the browser's own page zoom.
  useEffect(() => {
    const el = plotRef.current;
    if (!el || !fullDomain) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.deltaY === 0) return;
      e.preventDefault();
      const anchor = timeAt(e.clientX);
      if (anchor === null) return;
      const factor = e.deltaY < 0 ? 1 / WHEEL_ZOOM_STEP : WHEEL_ZOOM_STEP;
      setZoom((cur) => zoomAround(cur ?? fullDomain, fullDomain, anchor, factor));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // timeAt closes over `domain`; the deps that change it are listed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fullDomain, domain, status]);

  const emptySeries = (series ?? []).filter((s) => s.points.length === 0);
  const shellClass = className ?? "flex h-full min-h-[360px] w-full flex-col gap-3 rounded-[var(--radius-panel)] p-4";
  const shellStyle = { background: "var(--surface)", border: "1px solid var(--border)" };

  // The header (incl. range selector) stays mounted while loading so changing range doesn't flicker it away.
  return (
    <div className={shellClass} style={shellStyle}>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => dispatch({ type: "BACK_TO_GRAPH" })}
            className="flex items-center gap-1.5 rounded-[var(--radius-control)] px-2.5 py-1.5 text-xs font-medium"
            style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
          >
            <ArrowLeft size={12} weight="bold" aria-hidden="true" />
            Graph
          </button>
          <div
            role="tablist"
            aria-label="Chart view"
            className="flex gap-1 rounded-[var(--radius-control)] p-0.5"
            style={{ background: "var(--bg)", border: "1px solid var(--border)" }}
          >
            {(
              [
                ["main", "Price & odds"],
                ["correlation", "Correlation"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={view === value}
                onClick={() => setView(value)}
                className="rounded-[7px] px-3 py-1 text-xs font-medium"
                style={{
                  background: view === value ? "var(--surface)" : "transparent",
                  color: view === value ? "var(--text-primary)" : "var(--text-tertiary)",
                  boxShadow: view === value ? "0 0 0 1px var(--border)" : "none",
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {zoom && (
            <button
              type="button"
              onClick={() => setZoom(null)}
              className="flex items-center gap-1.5 rounded-[var(--radius-control)] px-2.5 py-1.5 text-xs font-medium"
              style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
              title="Back to the whole window (or double-click the chart)"
            >
              <ArrowsOutLineHorizontal size={12} weight="bold" aria-hidden="true" />
              Reset zoom
            </button>
          )}
          <DayStepper windowEnd={fullDomain ? fullDomain[1] : null} />
          <RangeSelector available={bundle?.availableRanges} />
        </div>
      </div>
      {status === "loading" || status === "idle" ? (
        <ChartSkeleton />
      ) : status === "error" || !series ? (
        <div className="flex flex-1 items-center justify-center" style={{ color: "var(--text-secondary)" }}>
          Couldn't load the comparison chart. Try again.
        </div>
      ) : (
        <>
          <ChartLegend entries={legendEntries} />
          {emptySeries.map((s) => (
            <p key={s.id} className="px-1 text-xs" style={{ color: "var(--text-tertiary)" }}>
              {s.label}: {emptyNote(s, bundle?.markets[s.id], domain)}
            </p>
          ))}
          {stock && domain ? (
            <div
              ref={plotRef}
              className={`flex flex-1 flex-col overflow-y-auto ${view === "main" ? "gap-0" : "gap-2"}`}
              onPointerDown={onBrushStart}
              onPointerMove={onBrushMove}
              onPointerUp={onBrushEnd}
              onPointerCancel={onBrushCancel}
              onDoubleClick={() => setZoom(null)}
              style={{ cursor: "crosshair", touchAction: "pan-y" }}
            >
              {view === "main" ? (
                <>
                  <PricePanel
                    showTimeAxis={markets.length === 0}
                    stock={stock}
                    domain={domain}
                    selection={selection}
                    color="var(--text-primary)"
                    height={markets.length > 0 ? 350 : 450}
                  >
                    {children}
                  </PricePanel>
                  {markets.length > 0 && (
                    <>
                      {/* No band between the panels: the label and zoom toggle float over the odds plot
                          instead of sitting in a row of their own. */}
                      <div className="relative flex" style={{ flex: "4 1 auto" }}>
                        <OddsPanel
                          markets={markets}
                          domain={domain}
                          selection={selection}
                          colorById={colorById}
                          showTimeAxis
                          height={300}
                          fit={fitOdds}
                        />
                        <span
                          className="pointer-events-none absolute left-16 top-2 text-xs font-medium uppercase tracking-wide"
                          style={{ color: "var(--text-tertiary)" }}
                        >
                          Odds
                        </span>
                        <div className="absolute right-4 top-2 z-10">
                          {/* Pressed = the full 0-100% scale; the default (fitted) is the unpressed state. */}
                          <button
                            type="button"
                            onClick={() => setFitOdds((v) => !v)}
                            aria-pressed={!fitOdds}
                            aria-label={fitOdds ? "Show the full 0-100% scale" : "Fit the odds axis to the data"}
                            title={fitOdds ? "Axis fitted to the data. Click for the full 0-100% scale" : "Showing 0-100%. Click to fit the axis to the data"}
                            className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-control)]"
                            style={{
                              color: fitOdds ? "var(--text-secondary)" : "var(--accent-contrast)",
                              background: fitOdds ? "transparent" : "var(--accent)",
                              border: "1px solid var(--border)",
                            }}
                          >
                            <MagnifyingGlassMinus size={15} weight="bold" />
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                </>
              ) : (
                <>
                  <RelationshipPanel
                    result={relationship?.result ?? null}
                    sampling={relationship?.sampling ?? null}
                    detail={relationship?.detail ?? null}
                    markets={markets}
                    selectedId={relationshipMarket?.id ?? null}
                    onSelect={setRelationshipPick}
                    domain={domain}
                    chartHeight={510}
                    color={
                      relationshipMarket ? (colorById.get(relationshipMarket.id) ?? "var(--accent)") : "var(--accent)"
                    }
                  />
                  <MethodsNote />
                </>
              )}
            </div>
          ) : (
            <div className="flex flex-1 items-center justify-center" style={{ color: "var(--text-secondary)" }}>
              No price data in this window.
            </div>
          )}
        </>
      )}
    </div>
  );
}

function MethodsNote() {
  return (
    <details className="px-1 text-xs" style={{ color: "var(--text-tertiary)" }}>
      <summary className="cursor-pointer">Methods</summary>
      <ul className="mt-1 list-disc space-y-1 pl-4">
        <li>
          Returns, not levels: prices and odds both trend, so correlating levels finds shared drift rather than
          co-movement. We correlate log returns (stock) with changes in logit odds.
        </li>
        <li>
          Delta-logit: a move from 94% to 97% carries more information than 50% to 53%; the logit stretches the ends of
          the 0-100 scale so equal changes mean roughly equal evidence.
        </li>
        <li>
          SPY-adjusted: the stock's SPY-explained return (fit by regression over the window) is removed, so the result
          is the stock-specific part, not just "both follow the market".
        </li>
        <li>
          95% band: Fisher z-transform of r with n = rolling window; it is skewed near +/-1, unlike r plus or minus a
          constant.
        </li>
        <li>
          Limitation: gaps are forward-filled, so odds with no trade repeat their last value and show a change of
          exactly 0, and the stock holds its last close through market trades outside exchange hours. Both pull r toward
          0 in thin markets; lead-lag steps are grid steps, not fixed durations. The best lag is picked from 13
          candidates, so treat it as exploratory.
        </li>
        <li>
          Young markets: a market open for less than a week is correlated over its last session at one-minute bars,
          whatever range is shown, because hourly bars give only about seven pairs per session.
        </li>
      </ul>
    </details>
  );
}
