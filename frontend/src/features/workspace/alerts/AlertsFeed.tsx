import { Bell, TrendDown, TrendUp, X } from "@phosphor-icons/react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AlertCompany, RecentAlert } from "../../../types/alert";
import { SourceMark } from "../shared/SourceMark";
import { useWorkspace } from "../useWorkspace";
import {
  DIRECTION_LABELS,
  TIER_LABELS,
  directionColor,
  summaryDetail,
  timeAgo,
} from "./alertFormat";
import { LIVE_WINDOW_MINUTES, useLiveAlerts } from "./LiveAlertsProvider";

/**
 * A tappable company chip. Tier 1 is solid (the market is about it); tiers 2
 * and 3 are outlined so a glance separates "directly about" from "exposed via
 * a link or a topic". Clicking it searches that ticker on the graph.
 */
function CompanyChip({
  company,
  onOpen,
}: {
  company: AlertCompany;
  onOpen: (symbol: string) => void;
}) {
  const direct = company.tier === 1;
  const why =
    company.tier === 1
      ? TIER_LABELS[1]
      : company.tier === 2
        ? `${TIER_LABELS[2]} of ${company.matched.join(", ")}`
        : `${TIER_LABELS[3]}${company.relevance !== null ? ` · ${Math.round(company.relevance * 100)}%` : ""}`;
  return (
    <button
      type="button"
      onClick={() => onOpen(company.symbol)}
      title={`${company.name ?? company.symbol} · ${why}`}
      className="rounded-full px-2 py-0.5 text-[10px] font-semibold tabular-nums"
      style={{
        background: direct ? "var(--accent)" : "transparent",
        color: direct ? "var(--accent-contrast)" : "var(--text-secondary)",
        border: `1px solid ${direct ? "var(--accent)" : "var(--border)"}`,
      }}
    >
      {company.symbol}
    </button>
  );
}

function AlertRow({
  alert,
  onOpen,
}: {
  alert: RecentAlert;
  onOpen: (symbol: string) => void;
}) {
  const up = alert.direction === "yes_up";
  const Trend = up ? TrendUp : TrendDown;
  const title = alert.eventTitle ?? alert.title ?? alert.marketId;
  return (
    <li
      className="flex flex-col gap-1.5 px-3 py-2.5"
      style={{
        background: "var(--bg)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-control)",
      }}
    >
      <div className="flex items-start gap-2">
        <SourceMark
          source={alert.source === "kalshi" ? "kalshi" : "polymarket"}
        />
        <div className="min-w-0 flex-1">
          {alert.url ? (
            <a
              href={alert.url}
              target="_blank"
              rel="noreferrer"
              className="line-clamp-2 text-xs font-semibold leading-snug hover:underline"
              style={{ color: "var(--text-primary)" }}
            >
              {title}
            </a>
          ) : (
            <h4
              className="line-clamp-2 text-xs font-semibold leading-snug"
              style={{ color: "var(--text-primary)" }}
            >
              {title}
            </h4>
          )}
        </div>
        <span
          className="shrink-0 text-[10px] tabular-nums"
          style={{ color: "var(--text-tertiary)" }}
        >
          {timeAgo(alert.createdAt)}
        </span>
      </div>
      <div
        className="flex items-center gap-2 pl-7 text-[10px]"
        style={{ color: "var(--text-tertiary)" }}
      >
        <span
          className="flex items-center gap-0.5 font-semibold"
          style={{ color: directionColor(alert.direction) }}
        >
          <Trend size={10} weight="bold" aria-hidden="true" />
          {DIRECTION_LABELS[alert.direction]}
        </span>
        <span className="truncate">{summaryDetail(alert.summary)}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1 pl-7">
        {alert.companies.length === 0 ? (
          <span
            className="text-[10px]"
            style={{ color: "var(--text-tertiary)" }}
          >
            No companies matched yet
          </span>
        ) : (
          alert.companies.map((c) => (
            <CompanyChip key={c.symbol} company={c} onOpen={onOpen} />
          ))
        )}
      </div>
    </li>
  );
}

/**
 * Live feed of market moves and the companies they may affect (spec
 * new_specs/alert-company-impact.md, UI option 3). A floating bell with the
 * count from the last hour opens a right-side drawer, the same pattern as the
 * evidence drawer: a modal would cover the graph the user wants to jump to.
 * Picking a company runs that ticker through the normal search path, so the
 * graph, tags and market recommendations all follow.
 */
export function AlertsFeed() {
  const { state, dispatch } = useWorkspace();
  const [open, setOpen] = useState(false);
  const { alerts, status } = useLiveAlerts();

  // Focus moves into the panel on open and back to the bell on close, like the evidence drawer.
  const panelRef = useRef<HTMLElement>(null);
  const bellRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      bellRef.current?.focus();
    };
  }, [open]);

  function openCompany(symbol: string) {
    setOpen(false);
    if (symbol !== state.ticker)
      dispatch({ type: "TICKER_SUBMITTED", ticker: symbol });
  }

  const count = alerts.length;

  return (
    <>
      <button
        ref={bellRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Open live alerts (${count} in the last hour)`}
        aria-expanded={open}
        // Once a search starts, a sidebar (markets, then evidence) owns the right edge; sit just left of it.
        // The sidebars are resizable and publish their width as a CSS variable (ResizableAside), so this
        // follows the edge as it's dragged. 20rem is their default width, for before one has mounted.
        // In market view the chart header (view tabs, day stepper, range) runs along the top, so drop below it.
        className={`absolute z-20 flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold shadow-sm ${state.stage === "market-view" ? "top-[4.75rem]" : "top-4"}`}
        style={{
          right:
            state.stage === "empty"
              ? "1rem"
              : `calc(var(${state.stage === "market-view" ? "--evidence-sidebar-width" : "--markets-sidebar-width"}, 20rem) + 1rem)`,
          background: "var(--surface)",
          border: "1px solid var(--border)",
          color: "var(--text-primary)",
        }}
      >
        <Bell
          size={14}
          weight={count > 0 ? "fill" : "regular"}
          aria-hidden="true"
        />
        Alerts
        {count > 0 && (
          <span
            className="rounded-full px-1.5 text-[10px] tabular-nums"
            style={{
              background: "var(--accent)",
              color: "var(--accent-contrast)",
            }}
          >
            {count >= 50 ? "50+" : count}
          </span>
        )}
      </button>

      {createPortal(
        <AnimatePresence>
          {open && (
            <>
              <motion.div
                key="backdrop"
                className="fixed inset-0 z-40"
                style={{ background: "rgba(0,0,0,0.25)" }}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setOpen(false)}
                aria-hidden="true"
              />
              <motion.aside
                key="panel"
                ref={panelRef}
                tabIndex={-1}
                role="dialog"
                aria-label="Live alerts"
                className="fixed inset-y-0 right-0 z-50 flex w-[26rem] max-w-full flex-col gap-2 px-4 py-4"
                style={{
                  background: "var(--surface)",
                  borderLeft: "1px solid var(--border)",
                }}
                initial={{ x: "100%" }}
                animate={{ x: 0 }}
                exit={{ x: "100%" }}
                transition={{ type: "spring", stiffness: 400, damping: 36 }}
              >
                <div className="flex items-center gap-2">
                  <Bell
                    size={16}
                    weight="fill"
                    aria-hidden="true"
                    style={{ color: "var(--text-primary)" }}
                  />
                  <span
                    className="text-[10px] font-semibold uppercase tracking-wider"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    Live alerts · last {LIVE_WINDOW_MINUTES} min
                  </span>
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    aria-label="Close alerts"
                    className="ml-auto"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    <X size={14} />
                  </button>
                </div>
                <p
                  className="text-[10px]"
                  style={{ color: "var(--text-tertiary)" }}
                >
                  Unusual prediction-market moves and the companies they may
                  affect. Click a ticker to open it on the graph.
                </p>
                {status === "loading" && alerts.length === 0 ? (
                  <p
                    className="py-3 text-xs"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    Loading…
                  </p>
                ) : status === "unavailable" && alerts.length === 0 ? (
                  <p
                    className="px-3 py-3 text-xs"
                    style={{
                      border: "1px dashed var(--border)",
                      borderRadius: "var(--radius-control)",
                      color: "var(--text-tertiary)",
                    }}
                  >
                    Alerts unavailable: the backend or its database can't be
                    reached.
                  </p>
                ) : alerts.length === 0 ? (
                  <p
                    className="px-3 py-3 text-xs"
                    style={{
                      border: "1px dashed var(--border)",
                      borderRadius: "var(--radius-control)",
                      color: "var(--text-tertiary)",
                    }}
                  >
                    Quiet hour: no unusual market moves yet.
                  </p>
                ) : (
                  <ul className="flex min-h-0 flex-col gap-1.5 overflow-y-auto pr-1">
                    {alerts.map((a) => (
                      <AlertRow key={a.id} alert={a} onOpen={openCompany} />
                    ))}
                  </ul>
                )}
              </motion.aside>
            </>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}
