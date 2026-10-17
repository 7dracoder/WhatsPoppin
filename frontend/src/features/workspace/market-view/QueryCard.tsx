import { Gauge, MagicWand } from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { ApiError } from "../../../lib/apiClient";
import { MAX_ITEMS, runEvidenceQuery, type QueryMode } from "../../../lib/api/classification";
import type { QueryResult } from "../../../types/project";
import { useWorkspace } from "../useWorkspace";

/**
 * Stage 9 — a free-text natural-language query run against the currently
 * added evidence set. Per the design spec this renders as a single stat
 * (percentage + n), not a chart — a quick gut-check, most recent result first.
 */
export function QueryCard({ action }: { action?: ReactNode }) {
  const { state, dispatch } = useWorkspace();
  const [query, setQuery] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [capped, setCapped] = useState(false);

  const hasEvidence = state.evidence.length > 0;

  async function run(mode: QueryMode) {
    const trimmed = query.trim();
    if (!hasEvidence || isRunning || (mode === "boolean" && !trimmed)) return;

    setIsRunning(true);
    setError(null);
    setCapped(false);
    try {
      const { percentage, n, label, capped } = await runEvidenceQuery(
        state.evidence.map((e) => e.item),
        mode === "boolean" ? trimmed : null,
        mode,
      );
      const result: QueryResult = {
        id: `query-${Date.now()}`,
        query: mode === "boolean" ? trimmed : "Sentiment",
        label,
        percentage,
        n,
        createdAt: new Date().toISOString(),
      };
      dispatch({ type: "QUERY_RESULT_ADDED", result });
      setCapped(capped);
      if (mode === "boolean") setQuery("");
    } catch (e) {
      // only 4xx reaches here (runEvidenceQuery mocks over network/5xx failures)
      setError(e instanceof ApiError ? e.message : "Query failed. Try again.");
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <div
      className="rounded-[var(--radius-panel)] border p-4"
      style={{ borderColor: "var(--border)", background: "var(--surface)" }}
    >
      <p className="text-xs font-medium uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>
        Query the evidence
      </p>

      <div className="mt-2 flex items-center gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") run("boolean");
          }}
          placeholder='e.g. "what % of these are positive sentiment"'
          disabled={!hasEvidence}
          className="flex-1 rounded-[var(--radius-control)] border px-3 py-2 text-sm outline-none"
          style={{
            borderColor: "var(--border)",
            background: "var(--surface-elevated)",
            color: "var(--text-primary)",
          }}
        />
        <button
          type="button"
          onClick={() => run("boolean")}
          disabled={!query.trim() || !hasEvidence || isRunning}
          className="flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-medium transition disabled:opacity-50"
          style={{ background: "var(--accent)", color: "var(--accent-contrast)" }}
        >
          <MagicWand size={14} weight="bold" />
          {isRunning ? "Running…" : "Run"}
        </button>
      </div>

      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => run("sentiment")}
          disabled={!hasEvidence || isRunning}
          className="flex items-center gap-1.5 rounded-[var(--radius-control)] border px-3 py-1.5 text-xs font-medium transition disabled:opacity-50"
          style={{ borderColor: "var(--border)", color: "var(--text-primary)" }}
        >
          <Gauge size={13} weight="bold" />
          Sentiment
        </button>
        {isRunning && (
          <span className="text-xs" style={{ color: "var(--text-tertiary)" }} role="status">
            Classifying {Math.min(state.evidence.length, MAX_ITEMS)} items — this can take a while…
          </span>
        )}
        {/* Right end of this row: an action owned by the page (the save button), so it scrolls with the card instead of floating over it. */}
        {action && <div className="ml-auto">{action}</div>}
      </div>

      {error && (
        <p className="mt-2 text-xs" style={{ color: "var(--status-negative)" }} role="alert">
          {error}
        </p>
      )}

      {capped && (
        <p className="mt-2 text-xs" style={{ color: "var(--text-tertiary)" }}>
          Only the newest {MAX_ITEMS} items were classified.
        </p>
      )}

      {!hasEvidence && (
        <p className="mt-2 text-xs" style={{ color: "var(--text-tertiary)" }}>
          Add some news or tweets first — queries run against the added evidence set.
        </p>
      )}

      {state.queryResults.length > 0 && (
        <ul className="mt-4 flex flex-col gap-2">
          {state.queryResults.map((result) => (
            <li
              key={result.id}
              className="flex items-center justify-between rounded-[var(--radius-control)] px-3 py-2"
              style={{ background: "var(--surface-elevated)" }}
            >
              <span className="min-w-0 truncate text-sm" style={{ color: "var(--text-secondary)" }}>
                {result.query}
              </span>
              <span className="shrink-0 pl-3 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                {result.percentage}%{" "}
                <span className="font-normal" style={{ color: "var(--text-tertiary)" }}>
                  (n={result.n})
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
