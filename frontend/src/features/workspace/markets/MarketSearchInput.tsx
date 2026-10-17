import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { MagnifyingGlass, Plus, Check, SmileySad } from "@phosphor-icons/react";
import { useWorkspace } from "../useWorkspace";
import { searchMarkets } from "../../../lib/api/markets";
import type { MarketCard } from "../../../types/market";

const DEBOUNCE_MS = 350;

/**
 * Stage 4 — free-text search for a market outside the suggested set.
 * Debounced so `searchMarkets` doesn't fire on every keystroke. An empty
 * result set gets an explicit empty state rather than a blank area, per the
 * design spec's acceptance criteria.
 */
export function MarketSearchInput() {
  const { state, dispatch } = useWorkspace();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MarketCard[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);

    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      setIsSearching(false);
      setHasSearched(false);
      return;
    }

    setIsSearching(true);
    debounceRef.current = setTimeout(() => {
      searchMarkets(trimmed).then((found) => {
        setResults(found);
        setIsSearching(false);
        setHasSearched(true);
      });
    }, DEBOUNCE_MS);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  return (
    <section className="flex flex-col gap-3">
      <label
        className="flex items-center gap-2 rounded-[var(--radius-control)] px-3 py-2"
        style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
      >
        <MagnifyingGlass size={16} style={{ color: "var(--text-tertiary)" }} />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search Kalshi markets to add your own…"
          className="w-full bg-transparent text-sm outline-none"
          style={{ color: "var(--text-primary)" }}
        />
      </label>

      <AnimatePresence>
        {query.trim().length > 0 && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            {isSearching ? (
              <p className="py-2 text-sm" style={{ color: "var(--text-tertiary)" }}>
                Searching…
              </p>
            ) : hasSearched && results.length === 0 ? (
              <div
                className="flex items-center gap-2 rounded-[var(--radius-control)] px-3 py-3 text-sm"
                style={{ border: "1px dashed var(--border)", color: "var(--text-tertiary)" }}
              >
                <SmileySad size={16} />
                No markets match "{query.trim()}".
              </div>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {results.map((market) => {
                  const alreadyAdded = state.selectedMarketIds.includes(market.marketId);
                  return (
                    <li
                      key={market.marketId}
                      className="flex items-center gap-3 rounded-[var(--radius-control)] p-2"
                      style={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)" }}
                    >
                      <img
                        src={market.thumbnailUrl}
                        alt=""
                        className="h-10 w-12 shrink-0 rounded object-cover"
                        style={{ backgroundColor: "var(--border)" }}
                        loading="lazy"
                      />
                      <span className="flex-1 text-sm" style={{ color: "var(--text-primary)" }}>
                        {market.title}
                      </span>
                      <button
                        type="button"
                        disabled={alreadyAdded}
                        onClick={() => dispatch({ type: "MARKET_ADDED", market })}
                        className="flex items-center gap-1 rounded-[var(--radius-pill)] px-2.5 py-1 text-xs font-medium"
                        style={{
                          backgroundColor: alreadyAdded ? "var(--border)" : "var(--accent)",
                          color: alreadyAdded ? "var(--text-tertiary)" : "var(--accent-contrast)",
                        }}
                      >
                        {alreadyAdded ? <Check size={12} weight="bold" /> : <Plus size={12} weight="bold" />}
                        {alreadyAdded ? "Added" : "Add"}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
