import { get } from "../../lib/apiClient";
import type {
  CompanyGraphResponse,
  CompanyRef,
  CompanySearchResponse,
  PairNewsResponse,
} from "../../types/graph";
import aapl from "./fixtures/AAPL.json";
import nvda from "./fixtures/NVDA.json";
import tsla from "./fixtures/TSLA.json";

/** With VITE_GRAPH_FAKE=1 the page reads local fixtures and calls no backend. */
export const GRAPH_FAKE = import.meta.env.VITE_GRAPH_FAKE === "1";

export const POLL_INTERVAL_MS = 1500;

const FIXTURES: Record<string, CompanyGraphResponse> = {
  AAPL: aapl as CompanyGraphResponse,
  NVDA: nvda as CompanyGraphResponse,
  TSLA: tsla as CompanyGraphResponse,
};

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Fake mode imitates a build in progress: the first response for a search is
 * "running" with half the links, the next one is the full "done" graph. That
 * exercises the polling path and the "new nodes appear" drawing.
 */
async function fakeGraph(
  ticker: string,
  attempt: number,
): Promise<CompanyGraphResponse> {
  await delay(250);
  const fixture = FIXTURES[ticker];
  if (!fixture) {
    return {
      company: { symbol: ticker, name: ticker },
      status: "done",
      nodes: [],
      links: [],
      highlights: [],
    };
  }
  if (attempt > 0) return fixture;
  const half = Math.ceil(fixture.links.length / 2);
  const links = fixture.links.slice(0, half);
  const targets = new Set(links.map((l) => l.target));
  return {
    ...fixture,
    status: "running",
    links,
    nodes: fixture.nodes.filter((n) => targets.has(n.symbol)),
    highlights: fixture.highlights.filter((h) => targets.has(h.target)),
  };
}

async function fakeSearch(q: string): Promise<CompanySearchResponse> {
  await delay(100);
  const needle = q.trim().toLowerCase();
  const seen = new Map<string, CompanyRef>();
  for (const f of Object.values(FIXTURES)) {
    seen.set(f.company.symbol, f.company);
    for (const n of f.nodes) {
      if (!seen.has(n.symbol)) seen.set(n.symbol, { symbol: n.symbol, name: n.name });
    }
  }
  return [...seen.values()]
    .filter(
      (c) =>
        c.symbol.toLowerCase().startsWith(needle) ||
        c.name.toLowerCase().includes(needle),
    )
    .slice(0, 10);
}

/**
 * GET /graph/{ticker}. `attempt` counts polls for this search (0 = first).
 * `linksOnly` asks the backend not to start a news run for this company, so
 * the answer is "done" as soon as its links are.
 */
export function fetchGraph(
  ticker: string,
  attempt: number,
  linksOnly = false,
): Promise<CompanyGraphResponse> {
  if (GRAPH_FAKE) return fakeGraph(ticker, attempt);
  return get<CompanyGraphResponse>(
    `/graph/${encodeURIComponent(ticker)}${linksOnly ? "?highlights=false" : ""}`,
  );
}

/**
 * GET /graph/{ticker}/news/{other}: recent news and X posts about the two
 * companies together. A new pair takes a few seconds; repeats are cached.
 */
export async function fetchPairNews(
  ticker: string,
  other: string,
): Promise<PairNewsResponse> {
  if (GRAPH_FAKE) {
    await delay(250);
    return {
      company: { symbol: ticker, name: ticker },
      other: { symbol: other, name: other },
      items: [],
      failed: [],
    };
  }
  return get<PairNewsResponse>(
    `/graph/${encodeURIComponent(ticker)}/news/${encodeURIComponent(other)}`,
  );
}

/** GET /companies/search?q= (up to 10 matches). */
export function searchCompanies(q: string): Promise<CompanySearchResponse> {
  if (GRAPH_FAKE) return fakeSearch(q);
  return get<CompanySearchResponse>(
    `/companies/search?q=${encodeURIComponent(q)}`,
  );
}
