export type MarketSource = "kalshi" | "polymarket";

/**
 * One prediction market, from either venue. Prices are in cents (1-99), which
 * double as the implied probability: a "Yes" share paying $1 that trades at
 * 62¢ means the market thinks the event is ~62% likely.
 */
export interface PredictionMarket {
  id: string; // Kalshi ticker (e.g. "KXFED-26DEC") or Polymarket slug
  source: MarketSource;
  title: string; // the question the venue displays as the market header
  category: string;
  // The top-markets endpoint stores no live quotes, so these are absent for real results
  // and the card hides them rather than showing a made-up number.
  yesPrice?: number; // cents, 1-99
  change24h?: number; // cents moved over the last 24h (signed)
  volume?: number; // total traded
  closeTime: string | null; // ISO 8601
  /** Which outcome of the event this market is; the venue's market title; its resolution rule. */
  outcome?: string | null;
  question?: string | null;
  rules?: string | null;
  /** Why it was recommended (set by GET /entities/{company}/top-markets). */
  tier?: 1 | 2 | 3; // 1 = about the company, 2 = a linked company, 3 = one of the tags
  matched?: string[]; // entity names that made it match
  url?: string | null;
}

/** A market returned by a free-text query, scored by the classifier (Jev). */
export interface PredictionMatch extends PredictionMarket {
  matchScore: number; // 0-1
  matchReason: string; // why the classifier thought it matched
}
