import type { CompanyGraph } from "../../types/workspaceGraph";
import { delay } from "./mockUtils";

/**
 * AAPL-specific tags, grounded in real, currently-live storylines (not
 * generic placeholders) so the mock demo reads as plausible rather than
 * obviously synthetic:
 * - CEO transition: John Ternus succeeded Tim Cook as CEO on 2026-09-01;
 *   Cook moved to executive chair. Ternus leads his first earnings call
 *   (fiscal Q4 FY26, reporting 2026-11-02) alongside CFO Kevan Parekh.
 * - "Year of AI" framing: Apple's July guidance projected September-quarter
 *   revenue growth of 9-11% YoY with iPhone revenue growing mid-teens; sell-
 *   side commentary (e.g. Wedbush) has framed 2026 as Apple's AI catch-up
 *   year after Siri/Apple Intelligence lagged competitors.
 * - App Store antitrust pressure and EU Digital Markets Act compliance
 *   remain live, ongoing regulatory threads independent of any one case.
 * - Supply chain diversification out of China (India/Vietnam assembly) is
 *   a multi-year, still-developing theme relevant to the supplier/customer
 *   edges in the mock graph.
 */
const AAPL_TAGS = [
  "CEO transition to John Ternus",
  "AI strategy catch-up",
  "iPhone revenue growth",
  "App Store antitrust pressure",
  "supply chain diversification",
  "EU Digital Markets Act compliance",
];

/**
 * Tags for the rest of the default recommended tickers (TicketInput's
 * quick-pick chips), grounded the same way as AAPL_TAGS above — real,
 * currently-live storylines rather than generic placeholders. Worded to
 * avoid repeating specific disputed figures where sources disagreed (e.g.
 * Tesla's exact Q2 gross margin, Copilot's exact paid-user count) — the
 * storyline itself is well corroborated even where one precise number isn't.
 */
const TSLA_TAGS = [
  "robotaxi margin economics",
  "automotive gross margin recovery",
  "2026 capex ramp ($25B+)",
  "Megapack / energy storage growth",
  "FSD and services mix shift",
];
const NVDA_TAGS = [
  "Blackwell demand ramp",
  "China export license restrictions",
  "data center capex supercycle",
  "H20/China chip licensing",
  "export-policy pushback",
];
const MSFT_TAGS = [
  "Azure growth acceleration",
  "Copilot adoption scale",
  "OpenAI Azure commitment ($250B)",
  "AI capex vs. margin concerns",
  "cloud capacity constraints",
];
const AMZN_TAGS = [
  "AWS reacceleration",
  "advertising revenue growth",
  "Anthropic investment gain",
  "retail margin expansion",
  "AI capex ramp",
];

const TICKER_TAGS: Record<string, string[]> = {
  AAPL: AAPL_TAGS,
  TSLA: TSLA_TAGS,
  NVDA: NVDA_TAGS,
  MSFT: MSFT_TAGS,
  AMZN: AMZN_TAGS,
};

/** Mocks POST /graphs/tags (Gemini call) — see frontend-architecture-spec.md Section 5. */
export async function generateTags(graph: CompanyGraph): Promise<string[]> {
  const centerId = graph.nodes.find((n) => n.isCenter)?.id?.toUpperCase();
  const curated = centerId ? TICKER_TAGS[centerId] : undefined;

  if (curated) {
    return delay(curated, 1400);
  }

  const hasSupplyChain = graph.edges.some((e) => e.relationship === "supplier" || e.relationship === "customer");
  const tags = [
    hasSupplyChain ? "supply chain disruption" : "market structure",
    "semiconductor exposure",
    "competitive pressure",
    "regulatory risk",
  ];
  return delay(tags, 1400);
}
