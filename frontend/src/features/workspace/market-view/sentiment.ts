import type { Sentiment } from "../../../types/content";

/**
 * Red/green for sentiment reuse the app's status tokens (the same hues as
 * market direction). Color alone isn't enough for color-blind users, so every
 * place that uses it also shows SENTIMENT_LABEL / a glyph.
 */
export const SENTIMENT_COLOR: Record<Sentiment, string> = {
  positive: "var(--status-positive)",
  negative: "var(--status-negative)",
  neutral: "var(--status-neutral)",
};

export const SENTIMENT_LABEL: Record<Sentiment, string> = {
  positive: "Positive",
  negative: "Negative",
  neutral: "Neutral",
};

export const SENTIMENT_GLYPH: Record<Sentiment, string> = {
  positive: "▲",
  negative: "▼",
  neutral: "•",
};
