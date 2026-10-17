import type { ContentItem, Sentiment } from "../../types/content";
import { post } from "../apiClient";

/** Mirrors backend/api/classification.py MAX_ITEMS: each item is one paid Jev call. */
const MAX_BATCH = 25;

// Per-session, keyed by item id. A tweet or article's sentiment doesn't change,
// so re-running Jev every time a tab reloads or a filter is reapplied would
// just spend money for the same answer. Mock fallbacks are deliberately NOT
// stored here, so a real answer replaces them once the backend is reachable.
const cache = new Map<string, Sentiment>();

/** Sentiment already known for an item, without triggering a call. */
export function cachedSentiment(id: string): Sentiment | undefined {
  return cache.get(id);
}

/**
 * POST /classification/sentiment: Jev's per-item label for every item we
 * haven't classified yet (capped at MAX_BATCH per call; the rest stay
 * uncolored). Falls back to a keyword guess on any failure, since the color is
 * decoration and shouldn't break the picker. Items Jev failed on are omitted.
 */
export async function classifySentiments(items: ContentItem[]): Promise<Map<string, Sentiment>> {
  const todo = items.filter((i) => !cache.has(i.id)).slice(0, MAX_BATCH);
  const fallback = new Map<string, Sentiment>();

  if (todo.length > 0) {
    try {
      const results = await post<{ label: Sentiment | null }[]>("/classification/sentiment", {
        items: todo.map(({ title, text }) => ({ title, text })),
      });
      todo.forEach((item, i) => {
        const label = results[i]?.label;
        if (label) cache.set(item.id, label);
      });
    } catch (error) {
      console.warn("POST /classification/sentiment unavailable, using keyword guesses", error);
      for (const item of todo) fallback.set(item.id, mockSentiment(item));
    }
  }

  const out = new Map<string, Sentiment>();
  for (const item of items) {
    const label = cache.get(item.id) ?? fallback.get(item.id);
    if (label) out.set(item.id, label);
  }
  return out;
}

const POSITIVE = [
  "beat",
  "beats",
  "raise",
  "raises",
  "strong",
  "bullish",
  "benefit",
  "easing",
  "growth",
  "surge",
  "upgrade",
];
const NEGATIVE = [
  "slip",
  "delay",
  "miss",
  "inquiry",
  "concern",
  "concerns",
  "disruption",
  "restrict",
  "antitrust",
  "recall",
  "lawsuit",
];

/** Crude lexicon stand-in for Jev so the colors are demonstrable without a backend. */
function mockSentiment(item: ContentItem): Sentiment {
  const words = `${item.title} ${item.text ?? ""}`.toLowerCase().split(/[^a-z]+/);
  const pos = words.filter((w) => POSITIVE.includes(w)).length;
  const neg = words.filter((w) => NEGATIVE.includes(w)).length;
  return pos > neg ? "positive" : neg > pos ? "negative" : "neutral";
}
