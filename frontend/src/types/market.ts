/** Subset of backend/models/market.py's Market, plus a thumbnail for the card picker. */
export interface MarketCard {
  marketId: string;
  /** The event's question ("What will Jensen Huang say during the GTC keynote?"). */
  title: string;
  category: string | null;
  thumbnailUrl: string;
  seriesTitle: string | null;
  closeTime: string | null; // ISO 8601
  /**
   * Which outcome of the event this market is ("AI", "$92,250 or above"): an event is
   * one question with one market per answer, and the chart plots the odds of this one.
   * Optional because projects saved before it existed don't have it.
   */
  outcome?: string | null;
  /** The market's own title, when it says more than the outcome ("Will he say 'AI'?"). */
  question?: string | null;
  /** The venue's resolution rule: what YES means, in the venue's words. */
  rules?: string | null;
  /** The market's page on its venue. */
  url?: string | null;
}

/** Visible window for the overlay chart; also selects the stock price tier. */
export type ChartRange = "1d" | "1w" | "1m" | "6m" | "1y" | "all";

/**
 * One point in a normalized series. `value` is a % change for stocks but a
 * percentage-POINT change for market odds (see lib/chart/align.ts for why).
 */
export interface NormalizedPoint {
  t: number; // epoch ms, on the shared time grid
  value: number; // change from the first point in the window (unit depends on series.unit)
  rawValue: number; // real units (USD price, or odds 0-100), shown on hover
  /**
   * Traded in the bar ending here (shares, or contracts). Unlike the price it is
   * NOT forward-filled: a grid step the series has no bar on has no volume.
   * Missing entirely for venues without a volume feed (Polymarket).
   */
  volume?: number;
}

export interface NormalizedSeries {
  id: string;
  label: string;
  kind: "stock" | "market";
  unit: "usd" | "probability";
  /** Empty when the series has no data in the window (e.g. a market with no trades). */
  points: NormalizedPoint[];
}
