import type { MarketCard } from "../../types/market";
import { delay } from "./mockUtils";

const CATALOG: MarketCard[] = [
  {
    marketId: "KX-CHIP-SHORTAGE",
    title: "Will global chip shortage worsen in Q2?",
    category: "Supply Chain",
    thumbnailUrl: "https://picsum.photos/seed/chip/160/120",
    seriesTitle: "Semiconductor Supply",
    closeTime: "2026-06-30T00:00:00Z",
  },
  {
    marketId: "KX-FED-RATE-JUL",
    title: "Fed cuts rates before July?",
    category: "Macro",
    thumbnailUrl: "https://picsum.photos/seed/fed/160/120",
    seriesTitle: "FOMC Decisions",
    closeTime: "2026-07-01T00:00:00Z",
  },
  {
    marketId: "KX-EV-TARIFF",
    title: "New EV tariffs announced this year?",
    category: "Trade Policy",
    thumbnailUrl: "https://picsum.photos/seed/ev/160/120",
    seriesTitle: "Trade Policy",
    closeTime: "2026-12-31T00:00:00Z",
  },
  {
    marketId: "KX-ANTITRUST",
    title: "Antitrust ruling against big tech by Q3?",
    category: "Regulatory",
    thumbnailUrl: "https://picsum.photos/seed/antitrust/160/120",
    seriesTitle: "Regulatory Actions",
    closeTime: "2026-09-30T00:00:00Z",
  },
  {
    marketId: "KX-EARNINGS-BEAT",
    title: "Beats consensus EPS next earnings call?",
    category: "Earnings",
    thumbnailUrl: "https://picsum.photos/seed/earnings/160/120",
    seriesTitle: "Earnings Season",
    closeTime: "2026-04-15T00:00:00Z",
  },
  {
    marketId: "KX-SUPPLIER-DEFAULT",
    title: "Key supplier misses delivery target this quarter?",
    category: "Supply Chain",
    thumbnailUrl: "https://picsum.photos/seed/supplier/160/120",
    seriesTitle: "Semiconductor Supply",
    closeTime: "2026-06-15T00:00:00Z",
  },
];

/** Mocks GET /markets/suggest — ranked by overlap with Gemini tags, see Section 5. */
export async function fetchSuggestedMarkets(tags: string[]): Promise<MarketCard[]> {
  const lowerTags = tags.map((t) => t.toLowerCase());
  const ranked = [...CATALOG].sort((a, b) => {
    const score = (m: MarketCard) =>
      lowerTags.some((t) => m.title.toLowerCase().includes(t.split(" ")[0])) ? 1 : 0;
    return score(b) - score(a);
  });
  return delay(ranked.slice(0, 5), 1100);
}

/** Mocks GET /markets/search — user's own free-text add, Stage 4. */
export async function searchMarkets(query: string): Promise<MarketCard[]> {
  const q = query.trim().toLowerCase();
  if (!q) return delay([], 200);
  const results = CATALOG.filter(
    (m) => m.title.toLowerCase().includes(q) || m.category?.toLowerCase().includes(q),
  );
  return delay(results, 500);
}
