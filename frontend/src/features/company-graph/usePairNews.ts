import { useEffect, useState } from "react";
import type { PairNewsResponse } from "../../types/graph";
import { fetchPairNews } from "./api";

export type PairNewsState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "done"; data: PairNewsResponse };

export const PAIR_SOURCE_LABEL: Record<string, string> = { news: "NewsAPI", x: "X" };

/**
 * GET /graph/{ticker}/news/{other} for one pair of linked companies: recent
 * news and X posts about the two together. Refetches when the pair changes;
 * pass `null` for either to skip (no request).
 */
export function usePairNews(ticker: string | null, other: string | null): PairNewsState {
  const [state, setState] = useState<PairNewsState>({ kind: "loading" });

  useEffect(() => {
    if (!ticker || !other) return;
    let cancelled = false;
    setState({ kind: "loading" });
    fetchPairNews(ticker, other)
      .then((data) => !cancelled && setState({ kind: "done", data }))
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, other]);

  return state;
}

export function formatPairDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}
