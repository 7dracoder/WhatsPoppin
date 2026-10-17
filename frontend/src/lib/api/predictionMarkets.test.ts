import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../apiClient";

vi.mock("../apiClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../apiClient")>()),
  get: vi.fn(),
}));
vi.mock("./mockUtils", () => ({ delay: <T,>(v: T) => Promise.resolve(v) }));

import { get } from "../apiClient";
import { fetchRecommendedMarkets } from "./predictionMarkets";

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
