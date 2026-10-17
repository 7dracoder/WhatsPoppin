import { useEffect } from "react";
import { motion } from "motion/react";
import { ArrowSquareOut, Check, ImageBroken } from "@phosphor-icons/react";
import { useWorkspace } from "../useWorkspace";
import { fetchSuggestedMarkets } from "../../../lib/api/markets";
import type { MarketCard } from "../../../types/market";

/**
 * Stage 4 — the suggested-market card grid.
 *
 * Fires `fetchSuggestedMarkets(tags)` once the Gemini tags (Stage 3) finish
 * loading. Cards, not a table row: per the design spec these markets are
 * being *chosen*, not scanned for data, so the thumbnail + title + checkbox
 * layout matters more than density.
 */
export function MarketCardGrid() {
  const { state, dispatch } = useWorkspace();

  useEffect(() => {
    if (state.tagsStatus !== "done" || state.marketsStatus !== "idle") return;

    let cancelled = false;
    dispatch({ type: "MARKETS_STATUS", status: "loading" });

    fetchSuggestedMarkets(state.tags)
      .then((markets) => {
        if (cancelled) return;
        dispatch({ type: "MARKETS_LOADED", markets });
      })
      .catch(() => {
        if (cancelled) return;
        dispatch({ type: "MARKETS_STATUS", status: "error" });
      });

    return () => {
      cancelled = true;
    };
  }, [state.tagsStatus, state.marketsStatus, state.tags, dispatch]);

  if (state.tagsStatus !== "done") return null;

  if (state.marketsStatus === "loading") {
    return <MarketGridSkeleton />;
  }

  if (state.marketsStatus === "error") {
    return (
      <p className="text-sm" style={{ color: "var(--status-negative)" }}>
        Couldn't load suggested markets. Try searching below instead.
      </p>
    );
  }

  if (state.marketsStatus === "done" && state.suggestedMarkets.length === 0) {
    return (
      <div
        className="rounded-[var(--radius-panel)] p-6 text-center text-sm"
        style={{ border: "1px dashed var(--border)", color: "var(--text-tertiary)" }}
      >
        No markets matched these tags yet. Use search below to add one.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {state.suggestedMarkets.map((market, i) => (
        <MarketCardTile
          key={market.marketId}
          market={market}
          selected={state.selectedMarketIds.includes(market.marketId)}
          index={i}
          onToggle={() => dispatch({ type: "MARKET_TOGGLED", marketId: market.marketId })}
        />
      ))}
    </div>
  );
}

interface MarketCardTileProps {
  market: MarketCard;
  selected: boolean;
  index: number;
  onToggle: () => void;
}

export function MarketCardTile({ market, selected, index, onToggle }: MarketCardTileProps) {
  return (
    <motion.button
      type="button"
      role="checkbox"
      aria-checked={selected}
      aria-label={market.title}
      onClick={onToggle}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.04 }}
      className="flex flex-col overflow-hidden rounded-[var(--radius-panel)] text-left transition-shadow"
      style={{
        backgroundColor: "var(--surface)",
        border: selected ? "2px solid var(--accent)" : "1px solid var(--border)",
      }}
    >
      <div className="relative aspect-[4/3] w-full overflow-hidden" style={{ backgroundColor: "var(--border)" }}>
        <img
          src={market.thumbnailUrl}
          alt=""
          className="h-full w-full object-cover"
          loading="lazy"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = "none";
          }}
        />
        <div
          className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-md"
          style={{
            backgroundColor: selected ? "var(--accent)" : "var(--surface)",
            border: selected ? "none" : "1px solid var(--border)",
          }}
        >
          {selected && <Check size={14} weight="bold" style={{ color: "var(--accent-contrast)" }} />}
        </div>
      </div>
      <div className="flex flex-1 flex-col gap-1 p-3">
        {market.category && (
          <span className="text-xs font-medium uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>
            {market.category}
          </span>
        )}
        <p className="text-sm font-semibold leading-snug" style={{ color: "var(--text-primary)" }}>
          {market.title}
        </p>
        {market.outcome && (
          // The event's question is the title; this is the answer whose odds the market trades.
          <p className="text-xs leading-snug" style={{ color: "var(--text-secondary)" }} title={market.rules ?? undefined}>
            Odds of: <span style={{ color: "var(--text-primary)" }}>{market.outcome}</span>
          </p>
        )}
        {market.url && (
          <a
            href={market.url}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()} // the card's own click toggles selection
            className="mt-auto flex items-center gap-1 self-start text-xs"
            style={{ color: "var(--text-tertiary)" }}
          >
            <ArrowSquareOut size={12} weight="bold" /> Open on the venue
          </a>
        )}
      </div>
    </motion.button>
  );
}

function MarketGridSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3" aria-hidden="true">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="flex flex-col overflow-hidden rounded-[var(--radius-panel)]"
          style={{ border: "1px solid var(--border)", backgroundColor: "var(--surface)" }}
        >
          <motion.div
            className="flex aspect-[4/3] w-full items-center justify-center"
            style={{ backgroundColor: "var(--border)" }}
            animate={{ opacity: [0.5, 0.85, 0.5] }}
            transition={{ duration: 1.3, repeat: Infinity, ease: "easeInOut", delay: i * 0.08 }}
          >
            <ImageBroken size={20} style={{ color: "var(--text-tertiary)" }} />
          </motion.div>
          <div className="flex flex-col gap-2 p-3">
            <motion.div
              className="h-3 w-16 rounded"
              style={{ backgroundColor: "var(--border)" }}
              animate={{ opacity: [0.5, 0.85, 0.5] }}
              transition={{ duration: 1.3, repeat: Infinity, ease: "easeInOut", delay: i * 0.08 }}
            />
            <motion.div
              className="h-4 w-full rounded"
              style={{ backgroundColor: "var(--border)" }}
              animate={{ opacity: [0.5, 0.85, 0.5] }}
              transition={{ duration: 1.3, repeat: Infinity, ease: "easeInOut", delay: i * 0.08 }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
