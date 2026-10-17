import { useEffect, useState, type FormEvent } from "react";
import { MagnifyingGlass, TrendDown, TrendUp, X } from "@phosphor-icons/react";
import {
  fetchRecommendedMarkets,
  matchMarkets,
  toMarketCard,
} from "../../../lib/api/predictionMarkets";
import type {
  PredictionMarket,
  PredictionMatch,
} from "../../../types/predictionMarket";
import { SourceMark } from "../shared/SourceMark";

const compact = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});
import { CompanyAlerts, LiveEventsList, useLiveAlerts } from "../alerts";
import { GenerateButton } from "../markets";
import { useWorkspace } from "../useWorkspace";

/** Recommendations shown (the list scrolls); matches the `limit` fetchRecommendedMarkets asks the backend for. */
const RECOMMENDED_MAX = 10;
/** A recommended market whose Yes price moved this many cents in 24h counts as a large move (flashes the searched company). */
const LARGE_CHANGE_CENTS = 5;

const TIER_LABELS: Record<1 | 2 | 3, string> = {
  1: "About the company",
  2: "Linked company",
  3: "Related topic",
};

function formatClose(iso: string | null): string {
  if (!iso) return "no close date";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      });
}

/**
 * One prediction market (Kalshi or Polymarket), kept to roughly three short lines so many fit in the
 * sidebar without scrolling: the market's question, then Yes price with its 24h
 * move and volume/close date, then (only for search results) the match.
 */
function MarketCard({
  market,
  selected,
  onToggle,
}: {
  market: PredictionMarket | PredictionMatch;
  selected: boolean;
  onToggle: () => void;
}) {
  const hasQuote = market.yesPrice !== undefined;
  const change = market.change24h ?? 0;
  const up = change > 0;
  const down = change < 0;
  const changeColor = up
    ? "var(--status-positive)"
    : down
      ? "var(--status-negative)"
      : "var(--status-neutral)";
  const match = "matchScore" in market ? market : null;

  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={selected}
        className="flex w-full flex-col gap-1 px-2.5 py-2 text-left"
        style={{
          background: "var(--bg)",
          // Same 1px border either way (no layout shift); the ring adds the
          // second pixel of weight so selection reads clearly without reflow.
          border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
          boxShadow: selected ? "0 0 0 1px var(--accent)" : "none",
          borderRadius: "var(--radius-control)",
        }}
      >
        <div className="flex items-start gap-2">
          <SourceMark source={market.source} />
          <h4
            className="line-clamp-2 text-xs font-semibold leading-snug"
            style={{ color: "var(--text-primary)" }}
          >
            {market.title}
          </h4>
        </div>

        {hasQuote ? (
          <div
            className="flex items-baseline gap-2 pl-7 text-[10px]"
            style={{ color: "var(--text-tertiary)" }}
          >
            <span
              className="text-sm font-semibold tabular-nums"
              style={{ color: "var(--text-primary)" }}
            >
              {market.yesPrice}%
            </span>
            <span
              className="flex items-center gap-0.5 tabular-nums"
              style={{ color: changeColor }}
            >
              {up && <TrendUp size={10} weight="bold" aria-hidden="true" />}
              {down && <TrendDown size={10} weight="bold" aria-hidden="true" />}
              {up ? "+" : ""}
              {change}¢
            </span>
            <span className="ml-auto tabular-nums">
              {market.volume !== undefined &&
                `Vol ${compact.format(market.volume)} · `}
              {formatClose(market.closeTime)}
            </span>
          </div>
        ) : (
          // Real recommendations carry no live quote: show why it matched instead of a price.
          <p
            className="truncate pl-7 text-[10px]"
            style={{ color: "var(--text-tertiary)" }}
          >
            {market.tier && (
              <span style={{ color: "var(--accent)" }}>
                {TIER_LABELS[market.tier]}
              </span>
            )}
            {market.matched &&
              market.matched.length > 0 &&
              ` · ${market.matched.join(", ")}`}
            {` · closes ${formatClose(market.closeTime)}`}
          </p>
        )}

        {match && (
          <p
            className="truncate pl-7 text-[10px]"
            style={{ color: "var(--text-tertiary)" }}
          >
            <span style={{ color: "var(--accent)" }}>
              {Math.round(match.matchScore * 100)}% match
            </span>
            {" · "}
            {match.matchReason}
          </p>
        )}
      </button>
    </li>
  );
}

/**
 * Full-height right-hand sidebar. Click cards to pick the markets to chart,
 * then Generate. Defaults to markets recommended for the
 * searched company; a free-text query instead runs every market through
 * the Jev classifier and lists the ones it judges relevant. Searches run on
 * submit rather than per keystroke because each one is a full classifier pass.
 */
export function PredictionMarketPanel({ ticker }: { ticker: string | null }) {
  const { state, dispatch } = useWorkspace();
  const { reportCenterMove } = useLiveAlerts();
  const tags = state.tags;
  const tagsKey = tags.join("\u0000");
  const [recs, setRecs] = useState<PredictionMarket[]>([]);
  const [recsAreSample, setRecsAreSample] = useState(false);
  const [recsLoading, setRecsLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [matches, setMatches] = useState<PredictionMatch[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setRecsLoading(true);
    fetchRecommendedMarkets(ticker, tags).then((found) => {
      if (cancelled) return;
      setRecs(found.markets);
      setRecsAreSample(found.isSample);
      setRecsLoading(false);
    });
    return () => {
      cancelled = true;
    };
    // Tags arrive after the graph settles; key on their content so a new array
    // with the same tags doesn't refetch (the endpoint may spend a Jev call matching them).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, tagsKey]);

  // Drop results from a superseded query so a slow earlier run can't overwrite a newer one.
  useEffect(() => {
    if (submitted === null) return;
    let cancelled = false;
    setSearching(true);
    matchMarkets(submitted).then((found) => {
      if (cancelled) return;
      setMatches(found);
      setSearching(false);
    });
    return () => {
      cancelled = true;
    };
  }, [submitted]);

  // A large 24h move in a recommended market flashes the searched company on the graph,
  // the same way a live alert flashes the companies it touches.
  useEffect(() => {
    const biggest = recs.reduce<PredictionMarket | null>(
      (best, m) =>
        Math.abs(m.change24h ?? 0) > Math.abs(best?.change24h ?? 0) ? m : best,
      null,
    );
    const change = biggest?.change24h ?? 0;
    reportCenterMove(
      ticker,
      // Sample markets are made up: a fake move must not pulse a real company.
      !recsAreSample && Math.abs(change) >= LARGE_CHANGE_CENTS ? biggest : null,
    );
    return () => reportCenterMove(null, null);
  }, [recs, recsAreSample, ticker, reportCenterMove]);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = query.trim();
    if (trimmed) setSubmitted(trimmed);
  }

  function clear() {
    setQuery("");
    setSubmitted(null);
    setMatches([]);
    setSearching(false);
  }

  const selectedCount = state.selectedMarketIds.length;

  function toggle(market: PredictionMarket) {
    if (state.selectedMarketIds.includes(market.id)) {
      dispatch({ type: "MARKET_TOGGLED", marketId: market.id });
    } else {
      dispatch({ type: "MARKET_ADDED", market: toMarketCard(market) });
    }
  }

  const showingSearch = submitted !== null;
  const loading = showingSearch ? searching : recsLoading;
  // Search results get the full list (the user asked for them); recommendations are capped so the live list fits.
  const items = showingSearch ? matches : recs.slice(0, RECOMMENDED_MAX);

  return (
    <section
      aria-label="Prediction market recommendations"
      className="flex h-full min-h-0 flex-col gap-2 px-4 py-4"
      style={{
        background: "var(--surface)",
        borderLeft: "1px solid var(--border)",
      }}
    >
      <div className="flex items-center gap-2">
        <SourceMark source="kalshi" />
        <SourceMark source="polymarket" />
        <span
          className="text-[10px] font-semibold uppercase tracking-wider"
          style={{ color: "var(--text-tertiary)" }}
        >
          Recommended
        </span>
        <span
          className="ml-auto flex shrink-0 items-center gap-2"
          style={{ color: "var(--text-tertiary)" }}
        >
          {selectedCount > 0 && (
            <span
              className="rounded-full px-2 py-0.5 text-[10px] font-semibold"
              style={{
                background: "var(--accent)",
                color: "var(--accent-contrast)",
              }}
            >
              {selectedCount} selected
            </span>
          )}
        </span>
      </div>

      <CompanyAlerts ticker={ticker} />

      <div className="flex min-h-0 flex-[3] flex-col gap-2">
        <form
          onSubmit={onSubmit}
          className="flex items-center gap-2 px-2.5 py-1.5"
          style={{
            background: "var(--bg)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-control)",
          }}
        >
          <MagnifyingGlass
            size={14}
            style={{ color: "var(--text-tertiary)" }}
            aria-hidden="true"
          />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Describe a risk or event…"
            aria-label="Search prediction markets with free text"
            className="w-full bg-transparent text-xs outline-none"
            style={{ color: "var(--text-primary)" }}
          />
          {(query || showingSearch) && (
            <button
              type="button"
              onClick={clear}
              aria-label="Clear search"
              style={{ color: "var(--text-tertiary)" }}
            >
              <X size={12} />
            </button>
          )}
        </form>

        <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
          {showingSearch
            ? searching
              ? `Matching “${submitted}” against every market…`
              : `${matches.length} ${matches.length === 1 ? "match" : "matches"} for “${submitted}”`
            : ticker
              ? `Recommended for ${ticker}${recsAreSample ? " · sample data (backend unavailable)" : ""}`
              : "Recommended markets"}
        </p>

        {loading ? (
          <p className="py-3 text-xs" style={{ color: "var(--text-tertiary)" }}>
            Loading…
          </p>
        ) : items.length === 0 ? (
          <p
            className="px-3 py-3 text-xs"
            style={{
              border: "1px dashed var(--border)",
              borderRadius: "var(--radius-control)",
              color: "var(--text-tertiary)",
            }}
          >
            {showingSearch
              ? `No markets matched “${submitted}”.`
              : `No markets found for ${ticker ?? "this company"} yet.`}
          </p>
        ) : (
          // max-h: a viewport of about four cards (10 are loaded) that scrolls, so the list neither
          // pushes the live-moves section down nor changes height as results change.
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] content-start max-h-[19rem] min-h-0 gap-1.5 overflow-y-auto pr-1">
            {items.map((m) => (
              <MarketCard
                key={m.id}
                market={m}
                selected={state.selectedMarketIds.includes(m.id)}
                onToggle={() => toggle(m)}
              />
            ))}
          </ul>
        )}

        <div
          className="shrink-0 border-t pt-3"
          style={{ borderColor: "var(--border)" }}
        >
          <GenerateButton />
        </div>
      </div>

      <div
        className="shrink-0"
        style={{ borderTop: "1px solid var(--border)" }}
      />

      <LiveEventsList />
    </section>
  );
}
