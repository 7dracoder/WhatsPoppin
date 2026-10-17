import { useMemo } from "react";
import { Lightning, TrendDown, TrendUp } from "@phosphor-icons/react";
import { toMarketCard } from "../../../lib/api/predictionMarkets";
import type { Alert, RecentAlert } from "../../../types/alert";
import type { PredictionMarket } from "../../../types/predictionMarket";
import { SourceMark } from "../shared/SourceMark";
import { useWorkspace } from "../useWorkspace";
import {
  DIRECTION_LABELS,
  directionColor,
  summaryDetail,
  timeAgo,
} from "./alertFormat";
import {
  LARGE_MOVE_SCORE,
  LIVE_WINDOW_MINUTES,
  useLiveAlerts,
} from "./LiveAlertsProvider";
import { useAutoScroll } from "./useAutoScroll";

/** An alert's market as a chartable PredictionMarket, so a live move can be charted like a recommendation. */
export function alertToMarket(alert: Alert): PredictionMarket {
  return {
    id: alert.marketId,
    source: alert.source === "kalshi" ? "kalshi" : "polymarket",
    title: alert.eventTitle ?? alert.title ?? alert.marketId,
    category: "",
    closeTime: null,
    url: alert.url,
  };
}

function LiveEventCard({
  alert,
  selected,
  tabbable = true,
  onToggle,
  onOpenCompany,
}: {
  alert: RecentAlert;
  selected: boolean;
  /** False in the repeated copy of the list: it still takes clicks, but Tab skips it. */
  tabbable?: boolean;
  onToggle: () => void;
  onOpenCompany: (symbol: string) => void;
}) {
  const Trend = alert.direction === "yes_up" ? TrendUp : TrendDown;
  const color = directionColor(alert.direction);
  return (
    <li>
      <div
        className="flex w-full flex-col gap-1 px-2.5 py-2 text-left"
        style={{
          background: "var(--bg)",
          border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
          boxShadow: selected ? "0 0 0 1px var(--accent)" : "none",
          borderRadius: "var(--radius-control)",
          // Same left bar as the node flash, so the list and the graph read as one signal.
          borderLeft: `3px solid ${color}`,
        }}
      >
        <button
          type="button"
          onClick={onToggle}
          aria-pressed={selected}
          tabIndex={tabbable ? undefined : -1}
          className="flex w-full items-start gap-2 text-left"
        >
          <SourceMark
            source={alert.source === "kalshi" ? "kalshi" : "polymarket"}
          />
          <h4
            className="line-clamp-2 text-xs font-semibold leading-snug"
            style={{ color: "var(--text-primary)" }}
          >
            {alert.eventTitle ?? alert.title ?? alert.marketId}
          </h4>
          <span
            className="ml-auto shrink-0 text-[10px] tabular-nums"
            style={{ color: "var(--text-tertiary)" }}
          >
            {timeAgo(alert.createdAt)}
          </span>
        </button>
        <p
          className="flex items-center gap-1.5 pl-7 text-[10px]"
          style={{ color: "var(--text-tertiary)" }}
        >
          <span
            className="flex shrink-0 items-center gap-0.5 whitespace-nowrap font-semibold"
            style={{ color }}
          >
            <Trend size={10} weight="bold" aria-hidden="true" />
            {DIRECTION_LABELS[alert.direction]}
          </span>
          <span className="min-w-0 truncate">
            {summaryDetail(alert.summary)}
          </span>
        </p>
        {alert.companies.length > 0 && (
          <p className="flex flex-wrap items-center gap-1 pl-7">
            {alert.companies.map((c) => (
              <button
                key={c.symbol}
                type="button"
                onClick={() => onOpenCompany(c.symbol)}
                tabIndex={tabbable ? undefined : -1}
                title={`${c.name ?? c.symbol}: open on the graph`}
                className="rounded-full px-1.5 text-[10px] font-semibold tabular-nums"
                style={{
                  background: c.tier === 1 ? "var(--accent)" : "transparent",
                  color:
                    c.tier === 1
                      ? "var(--accent-contrast)"
                      : "var(--text-secondary)",
                  border: `1px solid ${c.tier === 1 ? "var(--accent)" : "var(--border)"}`,
                }}
              >
                {c.symbol}
              </button>
            ))}
          </p>
        )}
      </div>
    </li>
  );
}

/**
 * The sidebar's bottom half: unusual market moves from the last hour at or
 * above the "large" score, newest first. Cards toggle into the chart selection
 * like recommendations do; a company chip opens that ticker on the graph.
 * A list too long for its box scrolls on its own and loops (useAutoScroll).
 */
export function LiveEventsList() {
  const { state, dispatch } = useWorkspace();
  const { large, status } = useLiveAlerts();
  const ids = useMemo(() => large.map((a) => a.id), [large]);
  const { scrollerRef, firstCopyRef, loops, scrollerProps } =
    useAutoScroll(ids);

  function toggle(alert: RecentAlert) {
    if (state.selectedMarketIds.includes(alert.marketId)) {
      dispatch({ type: "MARKET_TOGGLED", marketId: alert.marketId });
    } else {
      dispatch({
        type: "MARKET_ADDED",
        market: toMarketCard(alertToMarket(alert)),
      });
    }
  }

  function openCompany(symbol: string) {
    if (symbol !== state.ticker)
      dispatch({ type: "TICKER_SUBMITTED", ticker: symbol });
  }

  return (
    <section
      aria-label="Live market moves"
      // Takes up to ~40% of the sidebar when there are moves, and just its header row when quiet,
      // so recommendations keep the room on a short window.
      className={`flex min-h-0 flex-col gap-1.5 ${large.length > 0 ? "flex-[2] max-h-[45%]" : "shrink-0"}`}
    >
      <div className="flex items-center gap-1.5">
        <Lightning
          size={12}
          weight="fill"
          aria-hidden="true"
          style={{ color: "var(--status-warning)" }}
        />
        <span
          className="text-[10px] font-semibold uppercase tracking-wider"
          style={{ color: "var(--text-tertiary)" }}
        >
          Live · large moves · {LIVE_WINDOW_MINUTES} min
        </span>
        {large.length > 0 && (
          <span
            className="ml-auto text-[10px] tabular-nums"
            style={{ color: "var(--text-tertiary)" }}
          >
            {large.length}
          </span>
        )}
      </div>
      {status === "loading" && large.length === 0 ? (
        <p className="py-2 text-xs" style={{ color: "var(--text-tertiary)" }}>
          Listening…
        </p>
      ) : large.length === 0 ? (
        <p
          className="px-3 py-3 text-xs"
          style={{
            border: "1px dashed var(--border)",
            borderRadius: "var(--radius-control)",
            color: "var(--text-tertiary)",
          }}
        >
          {status === "unavailable"
            ? "Live alerts unavailable: the backend can't be reached."
            : `No market moved past score ${LARGE_MOVE_SCORE} in the last hour.`}
        </p>
      ) : (
        <div
          ref={scrollerRef}
          {...scrollerProps}
          className="flex min-h-0 flex-col gap-1.5 overflow-y-auto pr-1"
        >
          {/* Rendered twice when it overflows, so the auto-scroll can wrap around (useAutoScroll).
              The second copy is hidden from screen readers and the Tab order: same cards again. */}
          {(loops ? [false, true] : [false]).map((repeat) => (
            <ul
              key={String(repeat)}
              ref={repeat ? undefined : firstCopyRef}
              aria-hidden={repeat || undefined}
              className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] content-start shrink-0 gap-1.5"
            >
              {large.map((a) => (
                <LiveEventCard
                  key={a.id}
                  alert={a}
                  selected={state.selectedMarketIds.includes(a.marketId)}
                  tabbable={!repeat}
                  onToggle={() => toggle(a)}
                  onOpenCompany={openCompany}
                />
              ))}
            </ul>
          ))}
        </div>
      )}
    </section>
  );
}
