import { useEffect, useId, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { ArrowRight } from "@phosphor-icons/react";
import { motion } from "motion/react";
import { searchCompanies } from "../../company-graph/api";
import type { CompanyRef } from "../../../types/graph";
import { useWorkspace } from "../useWorkspace";

/** Keystroke-to-search delay: the directory search is cheap, but one request per keystroke is still noise. */
const SEARCH_DEBOUNCE_MS = 150;

/**
 * Stage 0 — the only thing on the empty canvas. Deliberately spare: the design
 * spec calls the emptiness around this input load-bearing (VISUAL_DENSITY: 3,
 * "art gallery" read), so this component renders nothing but the input itself —
 * no card, no extra chrome — and lets GraphCanvas's dot-grid background carry
 * the "this is a workspace" signal.
 */
const RECOMMENDED_TICKERS = ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN"];

export function TickerInput() {
  const { state, dispatch } = useWorkspace();
  const [value, setValue] = useState("");
  const [matches, setMatches] = useState<CompanyRef[]>([]);
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);
  const listId = useId();

  // Name or ticker -> candidates from GET /companies/search (SEC's listed companies plus the
  // seed aliases, so "google" finds GOOGL). Debounced; a stale response is dropped.
  useEffect(() => {
    const q = value.trim();
    if (!q) {
      setMatches([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      searchCompanies(q)
        .then((found) => {
          if (cancelled) return;
          setMatches(found);
          setActive(-1);
          setOpen(true);
        })
        .catch(() => {
          if (!cancelled) setMatches([]);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [value]);

  // Only live on Stage 0 ("empty") per the parent page's stage-gated composition —
  // but guard here too so this component is self-contained if re-mounted mid-flow.
  if (state.stage !== "empty") return null;

  function submit(ticker: string) {
    const trimmed = ticker.trim().replace(/^\$/, "").toUpperCase();
    if (!trimmed) return;
    setOpen(false);
    dispatch({ type: "TICKER_SUBMITTED", ticker: trimmed });
  }

  // Enter: the highlighted suggestion, else resolve what was typed. The lookup is awaited
  // here rather than read from the debounced `matches`, so Enter right after typing
  // "amazon" can't submit the literal word as a ticker. An exact ticker in the results
  // wins ("A" is Agilent, not the first name starting with "a"); otherwise the best match;
  // with no match at all the text goes through so the backend's 404 explains it.
  async function resolveAndSubmit(typed: string) {
    const upper = typed.replace(/^\$/, "").toUpperCase();
    const found =
      matches.length > 0
        ? matches
        : await searchCompanies(typed).catch(() => [] as CompanyRef[]);
    const exact = found.find(
      (c) => c.symbol === upper || c.symbol === upper.replace(".", "-"),
    );
    submit(exact?.symbol ?? found[0]?.symbol ?? typed);
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const typed = value.trim();
    if (!typed) return;
    if (active >= 0 && matches[active]) return submit(matches[active].symbol);
    void resolveAndSubmit(typed);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (!open || matches.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % matches.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i <= 0 ? matches.length - 1 : i - 1));
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    // The -50%/-50% recentering transform lives on this plain (non-motion)
    // wrapper, not the animated element below — motion/react takes full
    // ownership of `transform` on any element it animates x/y on, which
    // would otherwise silently wipe out the Tailwind translate classes that
    // pull the box back by half its own size. Keeping it on a separate,
    // un-animated element is what actually centers the box on its own
    // center point (against the full width/height of the pane) instead of
    // its top-left corner.
    <div className="absolute left-1/2 top-[45%] z-10 w-full max-w-md -translate-x-1/2 -translate-y-1/2 px-6">
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
      >
        <form
          onSubmit={handleSubmit}
          className="flex flex-col items-center gap-4"
        >
          <div className="relative w-full">
            <div
              className="flex w-full items-center gap-2 px-4 py-3"
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-control)",
              }}
            >
              <input
                autoFocus
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={handleKeyDown}
                onFocus={() => matches.length > 0 && setOpen(true)}
                onBlur={() => setOpen(false)}
                placeholder="Company name or ticker"
                maxLength={60}
                role="combobox"
                aria-expanded={open && matches.length > 0}
                aria-controls={listId}
                aria-activedescendant={
                  active >= 0 ? `${listId}-${active}` : undefined
                }
                aria-autocomplete="list"
                className="flex-1 bg-transparent text-sm outline-none"
                style={{ color: "var(--text-primary)" }}
              />
              <button
                type="submit"
                disabled={!value.trim()}
                aria-label="Build company graph"
                className="flex h-7 w-7 items-center justify-center rounded-full transition-opacity disabled:opacity-30"
                style={{
                  background: "var(--accent)",
                  color: "var(--accent-contrast)",
                }}
              >
                <ArrowRight size={14} weight="bold" />
              </button>
            </div>
            {open && matches.length > 0 && (
              <ul
                id={listId}
                role="listbox"
                aria-label="Matching companies"
                className="absolute left-0 right-0 top-full z-20 mt-1 max-h-64 overflow-y-auto py-1 shadow-lg"
                style={{
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-control)",
                }}
              >
                {matches.map((m, i) => (
                  <li
                    key={m.symbol}
                    id={`${listId}-${i}`}
                    role="option"
                    aria-selected={i === active}
                    // mousedown, not click: the input's blur closes the list before a click would land.
                    onMouseDown={(e) => {
                      e.preventDefault();
                      submit(m.symbol);
                    }}
                    onMouseEnter={() => setActive(i)}
                    className="flex cursor-pointer items-baseline gap-3 px-4 py-2 text-sm"
                    style={{
                      background:
                        i === active
                          ? "var(--surface-elevated)"
                          : "transparent",
                    }}
                  >
                    <span
                      className="w-16 shrink-0 font-mono text-xs tracking-wide"
                      style={{ color: "var(--accent)" }}
                    >
                      {m.symbol}
                    </span>
                    <span
                      className="truncate"
                      style={{ color: "var(--text-primary)" }}
                    >
                      {m.name}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            type a name like "nvidia" or a ticker; we'll map its supplier,
            competitor, and board network
          </p>
          <div className="flex flex-wrap items-center justify-center gap-2">
            {RECOMMENDED_TICKERS.map((ticker) => (
              <button
                key={ticker}
                type="button"
                onClick={() => submit(ticker)}
                className="rounded-[var(--radius-pill)] px-3 py-1.5 font-mono text-xs tracking-wide transition hover:opacity-80"
                style={{
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  color: "var(--text-secondary)",
                }}
              >
                {ticker}
              </button>
            ))}
          </div>
        </form>
      </motion.div>
    </div>
  );
}
