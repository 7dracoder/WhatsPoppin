import { useEffect, useState } from "react";
import type { CompanyGraphResponse } from "../../types/graph";
import { fetchGraph, POLL_INTERVAL_MS } from "./api";

export interface CompanyGraphState {
  data: CompanyGraphResponse | null;
  loading: boolean;
  error: string | null;
}

const IDLE: CompanyGraphState = { data: null, loading: false, error: null };

/**
 * Loads GET /graph/{ticker} and keeps polling every 1.5 s while the backend
 * reports status "running". Stops on "done", "error", or a ticker change.
 */
export function useCompanyGraph(ticker: string | null): CompanyGraphState {
  const [state, setState] = useState<CompanyGraphState>(IDLE);

  useEffect(() => {
    if (!ticker) {
      setState(IDLE);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setState({ data: null, loading: true, error: null });

    const poll = async (attempt: number) => {
      try {
        const data = await fetchGraph(ticker, attempt);
        if (cancelled) return;
        setState({ data, loading: false, error: null });
        if (data.status === "running") {
          timer = setTimeout(() => void poll(attempt + 1), POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        setState((prev) => ({
          data: prev.data,
          loading: false,
          error: err instanceof Error ? err.message : "Could not load the graph.",
        }));
      }
    };
    void poll(0);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [ticker]);

  return state;
}
