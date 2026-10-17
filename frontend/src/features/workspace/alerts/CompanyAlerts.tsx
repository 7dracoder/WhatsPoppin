import { useMemo } from "react";
import { ArrowSquareOut, TrendDown, TrendUp } from "@phosphor-icons/react";
import { toMarketCard } from "../../../lib/api/predictionMarkets";
import type { CompanyAlert } from "../../../types/alert";
import { SourceMark } from "../shared/SourceMark";
import { useWorkspace } from "../useWorkspace";
import {
  DIRECTION_LABELS,
  TIER_LABELS,
  directionColor,
  timeAgo,
} from "./alertFormat";
import { alertToMarket } from "./LiveEventsList";
import { useCompanyAlerts } from "./useAlerts";
import { useAutoScroll } from "./useAutoScroll";

/**
 * "Alerts affecting <ticker>" for the market sidebar (spec UI option 2): the
 * last day's unusual market moves matched back to the searched company, with
 * why it was matched. Renders nothing when there are none, so the sidebar
 * stays about recommendations until something actually moved.
 *
 * The box is about two cards tall; a longer list scrolls on its own and loops
 * (useAutoScroll), the same as the live list below. Cards toggle their market
 * into the chart selection like recommendations do.
 */
export function CompanyAlerts({ ticker }: { ticker: string | null }) {
  const { state, dispatch } = useWorkspace();
  const { alerts, status } = useCompanyAlerts(ticker);
  const ids = useMemo(() => alerts.map((a) => a.id), [alerts]);
  const { scrollerRef, firstCopyRef, loops, scrollerProps } =
    useAutoScroll(ids);
  if (!ticker || status !== "ready" || alerts.length === 0) return null;

  function toggle(alert: CompanyAlert) {
    if (state.selectedMarketIds.includes(alert.marketId)) {
      dispatch({ type: "MARKET_TOGGLED", marketId: alert.marketId });
    } else {
      dispatch({
        type: "MARKET_ADDED",
        market: toMarketCard(alertToMarket(alert)),
      });
    }
  }

  return (
    <section
      aria-label={`Alerts affecting ${ticker}`}
      className="flex shrink-0 flex-col gap-1.5"
    >
      <div className="flex items-center">
        <span
          className="text-[10px] font-semibold uppercase tracking-wider"
          style={{ color: "var(--text-tertiary)" }}
        >
          Alerts affecting {ticker} · 24h
        </span>
        <span
          className="ml-auto text-[10px] tabular-nums"
          style={{ color: "var(--text-tertiary)" }}
        >
          {alerts.length}
        </span>
      </div>
      <div
        ref={scrollerRef}
        {...scrollerProps}
        className="flex max-h-36 flex-col gap-1.5 overflow-y-auto pr-1"
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
            {alerts.map((a) => (
              <Row
                key={a.id}
                alert={a}
                selected={state.selectedMarketIds.includes(a.marketId)}
                tabbable={!repeat}
                onToggle={() => toggle(a)}
              />
            ))}
          </ul>
        ))}
      </div>
    </section>
  );
}

function Row({
  alert,
  selected,
  tabbable,
  onToggle,
}: {
  alert: CompanyAlert;
  selected: boolean;
  /** False in the repeated copy of the list: it still takes clicks, but Tab skips it. */
  tabbable: boolean;
  onToggle: () => void;
}) {
  const Trend = alert.direction === "yes_up" ? TrendUp : TrendDown;
  const title = alert.eventTitle ?? alert.title ?? alert.marketId;
  const why =
    alert.tier === 1
      ? TIER_LABELS[1]
      : alert.tier === 2
        ? `${TIER_LABELS[2]} via ${alert.matched.join(", ")}`
        : `${TIER_LABELS[3]}${alert.relevance !== null ? ` · ${Math.round(alert.relevance * 100)}%` : ""}`;
  return (
    <li
      className="flex flex-col gap-1 px-2.5 py-2"
      style={{
        background: "var(--bg)",
        border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
        boxShadow: selected ? "0 0 0 1px var(--accent)" : "none",
        borderRadius: "var(--radius-control)",
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
        <span
          className="line-clamp-2 text-xs font-semibold leading-snug"
          style={{ color: "var(--text-primary)" }}
        >
          {title}
        </span>
      </button>
      <p
        className="flex items-center gap-2 pl-7 text-[10px]"
        style={{ color: "var(--text-tertiary)" }}
      >
        <span
          className="flex shrink-0 items-center gap-0.5 whitespace-nowrap font-semibold"
          style={{ color: directionColor(alert.direction) }}
        >
          <Trend size={10} weight="bold" aria-hidden="true" />
          {DIRECTION_LABELS[alert.direction]}
        </span>
        <span className="min-w-0 truncate">{why}</span>
        <span className="ml-auto shrink-0 tabular-nums">
          {timeAgo(alert.createdAt)}
        </span>
        {/* The card itself selects the market, so the venue page gets its own link. */}
        {alert.url && (
          <a
            href={alert.url}
            target="_blank"
            rel="noreferrer"
            tabIndex={tabbable ? undefined : -1}
            aria-label="Open on the venue"
            title="Open on the venue"
            className="shrink-0"
          >
            <ArrowSquareOut size={11} aria-hidden="true" />
          </a>
        )}
      </p>
    </li>
  );
}
