/** Mirrors backend/api/alerts.py. An alert is one unusual market move the detector flagged. */

export type AlertDirection = "yes_up" | "yes_down";

/** 1 = the market is about the company, 2 = linked to a tier-1 company in the company graph, 3 = Jev judged it relevant. */
export type ImpactTier = 1 | 2 | 3;

export interface AlertCompany {
  symbol: string;
  name: string | null;
  tier: ImpactTier;
  /** Why: the tag (tier 1), the tier-1 companies it links to (tier 2), the market's tags (tier 3). */
  matched: string[];
  /** Tier 3 only: Jev's probability that the market matters to this company. */
  relevance: number | null;
}

export interface Alert {
  id: number;
  createdAt: string;
  /** "kalshi" | "polymarket" | "polymarket_us"; kept open since the detector adds venues. */
  source: string;
  marketId: string;
  title: string | null;
  eventTitle: string | null;
  url: string | null;
  direction: AlertDirection;
  summary: string;
  score: number;
}

export interface RecentAlert extends Alert {
  companies: AlertCompany[];
}

/** An alert as seen from one company: why that company was matched. */
export interface CompanyAlert extends Alert {
  tier: ImpactTier;
  matched: string[];
  relevance: number | null;
}
