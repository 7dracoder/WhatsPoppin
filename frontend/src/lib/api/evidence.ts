import type { ContentItem } from "../../types/content";
import { get } from "../apiClient";
import { delay } from "./mockUtils";

const DEFAULT_WINDOW_DAYS = 30;

export interface DateRange {
  start: string; // ISO 8601
  end: string;
}

/** Last 30 days unless the caller passes the chart's visible range. */
function resolveRange(range?: DateRange): DateRange {
  if (range) return range;
  const end = new Date();
  const start = new Date(end.getTime() - DEFAULT_WINDOW_DAYS * 86_400_000);
  return { start: start.toISOString(), end: end.toISOString() };
}

/**
 * What the sidebar lets the user narrow by. Each field mirrors something the
 * backend library already accepts, so these are real capabilities, not ones
 * invented for the UI:
 *  - tweets: backend/ingestion/twitter_lookup (X recent search query syntax,
 *    per-account lookup, date window)
 *  - news: backend/ingestion/news_api NewsQueryFilters (keywords/phrases,
 *    search_in, domains, exclude_domains, sort_by, date window)
 */
export interface TweetFilters {
  /** X search syntax: "exact phrase", #hashtag, $CASHTAG, joined with OR. Empty = `$TICKER OR TICKER`. */
  query: string;
  /** Only this account's posts (GET /twitter/account/{handle}); takes the place of the keyword search. */
  account: string;
  days: number;
}

export type NewsSearchField = "title" | "description" | "content";
export type NewsSort = "publishedAt" | "relevancy";

export interface NewsFilters {
  query: string;
  searchIn: NewsSearchField[];
  /** Comma-separated; prefix one with "-" to exclude it (NewsAPI's domains / exclude_domains). */
  domains: string;
  sortBy: NewsSort;
}

export const DEFAULT_TWEET_FILTERS: TweetFilters = { query: "", account: "", days: 30 };
export const DEFAULT_NEWS_FILTERS: NewsFilters = { query: "", searchIn: [], domains: "", sortBy: "publishedAt" };

function daysRange(days: number): DateRange {
  const end = new Date();
  return { start: new Date(end.getTime() - days * 86_400_000).toISOString(), end: end.toISOString() };
}

function terms(query: string): string[] {
  return query
    .toLowerCase()
    .split(/[^a-z0-9$#]+/)
    .filter((t) => t.length > 1 && t !== "or");
}

/**
 * Applies the filters in the browser. Used on the mock fallback, and for the
 * news filters the /evidence/news route doesn't forward to NewsQueryFilters
 * yet (it only takes ticker/start/end) — once it does, these become server-side.
 */
function applyTweetFilters(items: ContentItem[], f?: TweetFilters): ContentItem[] {
  if (!f) return items;
  const account = f.account.trim().replace(/^@/, "").toLowerCase();
  const q = terms(f.query);
  const since = Date.now() - f.days * 86_400_000;
  return items.filter((i) => {
    if (Date.parse(i.published_at) < since) return false;
    if (account) return (i.author ?? "").replace(/^@/, "").toLowerCase() === account;
    if (q.length === 0) return true;
    const hay = `${i.title} ${i.text}`.toLowerCase();
    return q.some((t) => hay.includes(t.replace(/^[$#]/, "")));
  });
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function applyNewsFilters(items: ContentItem[], f?: NewsFilters): ContentItem[] {
  if (!f) return items;
  const q = terms(f.query);
  const fields = f.searchIn.length > 0 ? f.searchIn : (["title", "description", "content"] as NewsSearchField[]);
  const wanted = f.domains
    .split(",")
    .map((d) => d.trim().toLowerCase())
    .filter(Boolean);
  const include = wanted.filter((d) => !d.startsWith("-"));
  const exclude = wanted.filter((d) => d.startsWith("-")).map((d) => d.slice(1));

  const hits = (i: ContentItem) => {
    // We only have a headline and one body field, so description and content both read `text`.
    const hay = fields
      .map((field) => (field === "title" ? i.title : (i.text ?? "")))
      .join(" ")
      .toLowerCase();
    return q.filter((t) => hay.includes(t)).length;
  };

  const kept = items.filter((i) => {
    const host = hostOf(i.url);
    if (include.length > 0 && !include.some((d) => host.endsWith(d))) return false;
    if (exclude.some((d) => host.endsWith(d))) return false;
    return q.length === 0 || hits(i) > 0;
  });
  // "Popularity" is a NewsAPI-side ranking we can't reproduce here, so it isn't offered.
  return kept.sort((a, b) =>
    f.sortBy === "relevancy" && q.length > 0
      ? hits(b) - hits(a)
      : Date.parse(b.published_at) - Date.parse(a.published_at),
  );
}

// Warn once per source so a down backend doesn't spam the console on every open.
const warned = new Set<string>();
function warnFallback(source: string, error: unknown) {
  if (warned.has(source)) return;
  warned.add(source);
  console.warn(`${source} unavailable, using mock candidates`, error);
}

/**
 * GET /twitter/search — candidate tweets for the pick-to-add list. Falls back to
 * mocks on any failure so the picker always works; an empty real result is kept
 * empty (it's a valid answer, not an error).
 */
export async function fetchCandidateTweets(
  ticker: string,
  range?: DateRange,
  filters?: TweetFilters,
): Promise<ContentItem[]> {
  if (!ticker.trim()) return []; // nothing to search for yet; the X API is rate-limited
  const { start, end } = resolveRange(range ?? (filters ? daysRange(filters.days) : undefined));
  const account = filters?.account.trim().replace(/^@/, "");
  // Cashtag form catches "$NVDA"; the bare ticker catches plain mentions.
  const query = filters?.query.trim() || `$${ticker} OR ${ticker}`;
  const window = `start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  try {
    return await get<ContentItem[]>(
      account
        ? `/twitter/account/${encodeURIComponent(account)}?${window}`
        : `/twitter/search?query=${encodeURIComponent(query)}&${window}`,
    );
  } catch (error) {
    warnFallback("GET /twitter/search", error);
    return applyTweetFilters(await mockCandidateTweets(ticker), filters);
  }
}

// NewsAPI's free plan reaches back one month, so the window is the last 30 days (the design spec's
// "older than 30 days only" rule can't be served on this plan and was dropped). Bounds are aligned
// to whole UTC days so every call in a day hits the same backend cache row (backend/evidence).
const NEWS_LOOKBACK_DAYS = 30;

function startOfUtcDay(ms: number): number {
  const d = new Date(ms);
  d.setUTCHours(0, 0, 0, 0);
  return d.getTime();
}

/** GET /evidence/news — candidate news from the last 30 days; same fallback policy as tweets. */
export async function fetchCandidateNews(
  ticker: string,
  range?: DateRange,
  filters?: NewsFilters,
): Promise<ContentItem[]> {
  if (!ticker.trim()) return []; // nothing to search for yet; NewsAPI calls are metered
  const today = startOfUtcDay(Date.now());
  const end = range?.end ?? new Date(today + 86_400_000).toISOString();
  const start = range?.start ?? new Date(today - NEWS_LOOKBACK_DAYS * 86_400_000).toISOString();
  try {
    const items = await get<ContentItem[]>(
      `/evidence/news?ticker=${encodeURIComponent(ticker)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
    );
    return applyNewsFilters(items, filters);
  } catch (error) {
    warnFallback("GET /evidence/news", error);
    return applyNewsFilters(await mockCandidateNews(ticker), filters);
  }
}

async function mockCandidateTweets(ticker: string): Promise<ContentItem[]> {
  const now = Date.now();
  const items: ContentItem[] = [
    {
      source: "twitter",
      id: "tw-1",
      author: "@marketwatcher",
      title: `${ticker} supplier flagged a delivery slip this week`,
      text: `Hearing ${ticker}'s key supplier missed a delivery window. Watching for guidance impact.`,
      entities: [ticker],
      url: "https://twitter.com/marketwatcher/status/1",
      published_at: new Date(now - 4 * 86_400_000).toISOString(),
    },
    {
      source: "twitter",
      id: "tw-2",
      author: "@chipfloor",
      title: `Semi supply chain update mentions ${ticker}`,
      text: `Capacity easing across the board, ${ticker} should benefit next quarter.`,
      entities: [ticker],
      url: "https://twitter.com/chipfloor/status/2",
      published_at: new Date(now - 9 * 86_400_000).toISOString(),
    },
    {
      source: "twitter",
      id: "tw-3",
      author: "@regwatch",
      title: `Antitrust chatter touches ${ticker}'s sector`,
      text: `Regulators circling the sector again — ${ticker} named in passing.`,
      entities: [ticker],
      url: "https://twitter.com/regwatch/status/3",
      published_at: new Date(now - 14 * 86_400_000).toISOString(),
    },
    {
      source: "twitter",
      id: "tw-4",
      author: "@marketwatcher",
      title: `${ticker} options flow turns bullish`,
      text: `Unusual call volume in ${ticker} ahead of earnings. $${ticker} #options`,
      entities: [ticker],
      url: "https://twitter.com/marketwatcher/status/4",
      published_at: new Date(now - 2 * 86_400_000).toISOString(),
    },
    {
      source: "twitter",
      id: "tw-5",
      author: "@macrodesk",
      title: `Rate-cut odds move ${ticker} and peers`,
      text: `Fed pricing shifted overnight; growth names like ${ticker} reacting. #fomc`,
      entities: [ticker],
      url: "https://twitter.com/macrodesk/status/5",
      published_at: new Date(now - 6 * 86_400_000).toISOString(),
    },
  ];
  return delay(items, 700);
}

async function mockCandidateNews(ticker: string): Promise<ContentItem[]> {
  const now = Date.now();
  const items: ContentItem[] = [
    {
      source: "news",
      id: "news-1",
      author: null,
      title: `${ticker} beats earnings estimates on strong demand`,
      text: "Quarterly results came in ahead of consensus, driven by demand in core segments.",
      entities: [ticker],
      url: "https://example-news.com/earnings",
      published_at: new Date(now - 35 * 86_400_000).toISOString(),
    },
    {
      source: "news",
      id: "news-2",
      author: null,
      title: `Regulators open inquiry touching ${ticker}'s industry`,
      text: "A new regulatory inquiry could affect several firms in the sector, including this one.",
      entities: [ticker],
      url: "https://markets.example.org/regulatory",
      published_at: new Date(now - 50 * 86_400_000).toISOString(),
    },
    {
      source: "news",
      id: "news-3",
      author: null,
      title: `Supplier disruption raises cost concerns for ${ticker}`,
      text: "A key supplier's delivery delay is raising near-term cost concerns.",
      entities: [ticker],
      url: "https://wire.example.net/supplier",
      published_at: new Date(now - 70 * 86_400_000).toISOString(),
    },
    {
      source: "news",
      id: "news-4",
      author: null,
      title: `Analysts raise ${ticker} price targets after product event`,
      text: "Several brokers lifted targets, citing stronger-than-expected demand signals.",
      entities: [ticker],
      url: "https://markets.example.org/analysts",
      published_at: new Date(now - 42 * 86_400_000).toISOString(),
    },
    {
      source: "news",
      id: "news-5",
      author: null,
      title: `Opinion: is ${ticker} priced for perfection?`,
      text: "A look at valuation after a strong run, and what could disappoint.",
      entities: [ticker],
      url: "https://blog.example.com/priced-for-perfection",
      published_at: new Date(now - 58 * 86_400_000).toISOString(),
    },
  ];
  return delay(items, 700);
}
