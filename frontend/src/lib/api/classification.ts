import type { ContentItem } from "../../types/content";
import { ApiError, post } from "../apiClient";
import { delay } from "./mockUtils";

export type QueryMode = "sentiment" | "boolean";

export interface QueryOutcome {
  percentage: number;
  n: number;
  label: string;
  /** True when more than MAX_ITEMS were supplied and only the newest were classified. */
  capped: boolean;
}

/** The backend rejects larger batches (each item is an LLM call). */
export const MAX_ITEMS = 25;

/**
 * POST /classification/query. Network/5xx failures fall back to a deterministic
 * mock so the demo keeps working; 4xx (e.g. boolean mode without a query) is the
 * caller's mistake, so it propagates as an ApiError for the UI to show.
 */
export async function runEvidenceQuery(
  items: ContentItem[],
  query: string | null,
  mode: QueryMode,
): Promise<QueryOutcome> {
  // Newest first, so the cap drops the stalest evidence.
  const sorted = [...items].sort((a, b) => b.published_at.localeCompare(a.published_at));
  const batch = sorted.slice(0, MAX_ITEMS);
  const capped = items.length > MAX_ITEMS;

  try {
    const result = await post<{ percentage: number; n: number; label: string }>("/classification/query", {
      items: batch.map(({ title, text }) => ({ title, text })),
      query,
      mode,
    });
    return { ...result, capped };
  } catch (error) {
    if (error instanceof ApiError && error.status >= 400 && error.status < 500) throw error;
    console.warn("POST /classification/query unavailable, using mock result", error);
    return { ...(await mockQuery(batch.length, query, mode)), capped };
  }
}

async function mockQuery(n: number, query: string | null, mode: QueryMode) {
  const label = mode === "sentiment" ? "Positive sentiment" : (query ?? "");
  if (n === 0) return delay({ percentage: 0, n: 0, label }, 300);
  // deterministic fake score from the label so repeated runs are stable
  const hash = label.split("").reduce((acc, ch) => acc + ch.charCodeAt(0), 0);
  return delay({ percentage: (hash % 60) + 20, n, label }, 1000);
}
