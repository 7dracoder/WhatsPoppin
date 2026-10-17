import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../apiClient";

vi.mock("../apiClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../apiClient")>()),
  get: vi.fn(),
}));
vi.mock("./mockUtils", () => ({ delay: <T,>(v: T) => Promise.resolve(v) }));

import { get } from "../apiClient";
import { fetchRecommendedMarkets, matchMarkets } from "./predictionMarkets";

const mockGet = vi.mocked(get);

describe("fetchRecommendedMarkets", () => {
  beforeEach(() => {
    mockGet.mockReset();
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  it("maps top-markets rows and sends each tag as a repeated query key", async () => {
    mockGet.mockResolvedValue({
      markets: [
        {
          source: "polymarket_us",
          market_id: "m1",
          event_title: "Will oil top $100?",
          title: "Yes",
          outcome_label: null,
          close_time: "2026-12-31T00:00:00Z",
          url: "https://x",
          tier: 3,
          matched: ["Crude Oil"],
        },
      ],
    });
    const result = await fetchRecommendedMarkets("XOM", ["oil", "ev"]);
    expect(mockGet).toHaveBeenCalledWith("/entities/XOM/top-markets?limit=10&tags=oil&tags=ev");
    expect(result.isSample).toBe(false);
    expect(result.markets[0]).toMatchObject({ id: "m1", source: "polymarket", title: "Will oil top $100?", tier: 3 });
    expect(result.markets[0].yesPrice).toBeUndefined(); // never fabricate a quote for real results
  });

  it("keeps a 404 or an empty list empty instead of padding with samples", async () => {
    mockGet.mockRejectedValueOnce(new ApiError(404, "nope"));
    expect(await fetchRecommendedMarkets("ZZZZ")).toEqual({ markets: [], isSample: false });
    mockGet.mockResolvedValueOnce({ markets: [] });
    expect(await fetchRecommendedMarkets("XOM")).toEqual({ markets: [], isSample: false });
  });

  it("falls back to labeled sample markets when the backend is down", async () => {
    mockGet.mockRejectedValue(new ApiError(503, "no db"));
    const result = await fetchRecommendedMarkets("NVDA");
    expect(result.isSample).toBe(true);
    expect(result.markets.length).toBeGreaterThan(0);
  });
});

describe("matchMarkets", () => {
  beforeEach(() => {
    mockGet.mockReset();
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  it("maps /markets/search rows and keeps the similarity score", async () => {
    mockGet.mockResolvedValue([
      {
        source: "polymarket_us",
        market_id: "m1",
        event_id: "e1",
        title: "Fed cuts 25bp?",
        outcome_label: "Yes",
        event_title: "Fed decision in December?",
        rules_primary: null,
        close_time: null,
        url: null,
        score: 0.38,
        model: "voyage/voyage-finance-2",
      },
    ]);
    const [m] = await matchMarkets("interest rate cut");
    expect(mockGet).toHaveBeenCalledWith("/markets/search?q=interest%20rate%20cut&limit=10");
    expect(m).toMatchObject({ id: "m1", source: "polymarket", title: "Fed decision in December?", matchScore: 0.38 });
    expect(m.matchReason).toContain("voyage-finance-2");
  });

  it("keeps an empty real answer empty", async () => {
    mockGet.mockResolvedValue([]);
    expect(await matchMarkets("nothing like this")).toEqual([]);
  });

  it("falls back to labeled sample matches when the backend is down", async () => {
    mockGet.mockRejectedValue(new ApiError(503, "down"));
    const found = await matchMarkets("chip export china");
    expect(found.length).toBeGreaterThan(0);
    expect(found[0].matchReason).toMatch(/^Sample data/);
  });
});
