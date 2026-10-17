import { describe, expect, it } from "vitest";
import {
  initialWorkspaceState,
  type EvidenceAnnotation,
} from "../../types/project";
import { shiftDay, workspaceReducer } from "./workspaceReducer";

function annotation(itemId: string): EvidenceAnnotation {
  return {
    id: `evidence-${itemId}-1`,
    timestamp: "2026-09-01T00:00:00Z",
    item: {
      source: "news",
      id: itemId,
      author: null,
      title: itemId,
      text: null,
      entities: [],
      url: `https://e.com/${itemId}`,
      published_at: "2026-09-01T00:00:00Z",
    },
  };
}

describe("EVIDENCE_REMOVED", () => {
  const withTwo = {
    ...initialWorkspaceState,
    evidence: [annotation("a"), annotation("b")],
  };

  it("removes the evidence for that item id and keeps the rest", () => {
    const next = workspaceReducer(withTwo, {
      type: "EVIDENCE_REMOVED",
      itemId: "a",
    });
    expect(next.evidence.map((e) => e.item.id)).toEqual(["b"]);
  });

  it("closes the drawer if it was showing the removed evidence", () => {
    const pinned = { ...withTwo, pinnedAnnotationId: "evidence-a-1" };
    expect(
      workspaceReducer(pinned, { type: "EVIDENCE_REMOVED", itemId: "a" })
        .pinnedAnnotationId,
    ).toBeNull();
  });

  it("leaves the drawer alone when a different item is removed", () => {
    const pinned = { ...withTwo, pinnedAnnotationId: "evidence-b-1" };
    expect(
      workspaceReducer(pinned, { type: "EVIDENCE_REMOVED", itemId: "a" })
        .pinnedAnnotationId,
    ).toBe("evidence-b-1");
  });

  it("is a no-op (same state object) for an id that isn't there", () => {
    expect(
      workspaceReducer(withTwo, { type: "EVIDENCE_REMOVED", itemId: "zzz" }),
    ).toBe(withTwo);
  });
});

describe("MARKET_SWAPPED", () => {
  const card = (id: string) => ({
    marketId: id, title: "What will Jensen say?", category: null, thumbnailUrl: "", seriesTitle: null, closeTime: null,
  });
  const base = {
    ...initialWorkspaceState,
    suggestedMarkets: [card("AI"), card("OTHER")],
    selectedMarketIds: ["AI", "OTHER"],
  };

  it("replaces the market in place in both the cards and the selection", () => {
    const next = workspaceReducer(base, { type: "MARKET_SWAPPED", marketId: "AI", market: card("TSM") });
    expect(next.suggestedMarkets.map((m) => m.marketId)).toEqual(["TSM", "OTHER"]);
    expect(next.selectedMarketIds).toEqual(["TSM", "OTHER"]);
  });

  it("swapping to a market that is already selected just drops the old one", () => {
    const next = workspaceReducer(base, { type: "MARKET_SWAPPED", marketId: "AI", market: card("OTHER") });
    expect(next.suggestedMarkets.map((m) => m.marketId)).toEqual(["OTHER"]);
    expect(next.selectedMarketIds).toEqual(["OTHER"]);
  });

  it("is a no-op for the same market", () => {
    expect(workspaceReducer(base, { type: "MARKET_SWAPPED", marketId: "AI", market: card("AI") })).toBe(base);
  });
});

describe("GRAPH_EXPANDED", () => {
  const graph = {
    nodes: [
      { id: "NVDA", kind: "company" as const, label: "NVDA", isCenter: true },
    ],
    edges: [],
  };
  const base = {
    ...initialWorkspaceState,
    ticker: "NVDA",
    stage: "graph-ready" as const,
    graph,
  };
  const tsm = {
    nodes: [
      { id: "TSM", kind: "company" as const, label: "TSM", isCenter: true },
    ],
    edges: [],
  };
  const asml = {
    nodes: [
      { id: "ASML", kind: "company" as const, label: "ASML", isCenter: true },
    ],
    edges: [],
  };

  it("merges the addition into the current graph, keeping one center", () => {
    const next = workspaceReducer(base, {
      type: "GRAPH_EXPANDED",
      ticker: "NVDA",
      addition: tsm,
    });
    expect(next.graph?.nodes.map((n) => [n.id, !!n.isCenter])).toEqual([
      ["NVDA", true],
      ["TSM", false],
    ]);
  });

  it("two expansions in a row both survive (merged against state, not a stale base)", () => {
    const a = workspaceReducer(base, {
      type: "GRAPH_EXPANDED",
      ticker: "NVDA",
      addition: tsm,
    });
    const b = workspaceReducer(a, {
      type: "GRAPH_EXPANDED",
      ticker: "NVDA",
      addition: asml,
    });
    expect(b.graph?.nodes.map((n) => n.id)).toEqual(["NVDA", "TSM", "ASML"]);
  });

  it("ignores an expansion for a ticker the user has moved on from", () => {
    expect(
      workspaceReducer(base, {
        type: "GRAPH_EXPANDED",
        ticker: "AAPL",
        addition: tsm,
      }),
    ).toBe(base);
  });

  it("is a no-op when the merge found nothing new", () => {
    expect(
      workspaceReducer(base, {
        type: "GRAPH_EXPANDED",
        ticker: "NVDA",
        addition: graph,
      }),
    ).toBe(base);
  });
});

describe("chart day cursor", () => {
  const NOW = Date.parse("2026-10-11T03:00:00Z"); // a Sunday

  it("steps back from now to yesterday", () => {
    expect(shiftDay(null, -1, NOW)).toBe("2026-10-10");
    expect(shiftDay("2026-10-10", -1, NOW)).toBe("2026-10-09");
  });

  it("stepping forward onto today returns to now", () => {
    expect(shiftDay("2026-10-10", 1, NOW)).toBeNull();
    expect(shiftDay(null, 1, NOW)).toBeNull();
  });

  it("CHART_DAY_RESET clears the cursor and is a no-op at now", () => {
    const back = workspaceReducer(initialWorkspaceState, {
      type: "CHART_DAY_SHIFTED",
      days: -1,
    });
    expect(back.chartDay).not.toBeNull();
    expect(
      workspaceReducer(back, { type: "CHART_DAY_RESET" }).chartDay,
    ).toBeNull();
    expect(
      workspaceReducer(initialWorkspaceState, { type: "CHART_DAY_RESET" }),
    ).toBe(initialWorkspaceState);
  });
});

describe("GRAPH_LOADED", () => {
  const node = (id: string, isCenter = false) => ({
    id,
    label: id,
    kind: "company" as const,
    isCenter,
  });
  const graph = (ids: string[], highlights: never[] = []) => ({
    nodes: [node("NVDA", true), ...ids.map((id) => node(id))],
    edges: [],
    highlights,
  });
  const searching = workspaceReducer(initialWorkspaceState, {
    type: "TICKER_SUBMITTED",
    ticker: "NVDA",
  });
  const loaded = (
    state: typeof searching,
    ids: string[],
    linksDone: boolean,
    highlights: never[] = [],
  ) =>
    workspaceReducer(state, {
      type: "GRAPH_LOADED",
      ticker: "NVDA",
      graph: graph(ids, highlights),
      status: "running",
      linksDone,
    });

  it("is still loading while links can arrive", () => {
    const next = loaded(searching, ["TSM"], false);
    expect(next.graphStatus).toBe("loading");
    expect(next.graphNewsRunning).toBe(false);
  });

  it("is done once the links are in, even though the news run keeps the backend 'running'", () => {
    const next = loaded(searching, ["TSM"], true);
    expect(next.graphStatus).toBe("done");
    expect(next.graphNewsRunning).toBe(true);
  });

  it("takes only the highlights from later responses, keeping companies merged in since", () => {
    const done = loaded(searching, ["TSM", "AMD"], true);
    const highlight = { target_symbol: "TSM" } as never;
    const next = loaded(done, ["TSM"], true, [highlight]);
    expect(next.graph?.nodes.map((n) => n.id)).toEqual(["NVDA", "TSM", "AMD"]);
    expect(next.graph?.highlights).toEqual([highlight]);
  });
});

describe("BACK_TO_GRAPH", () => {
  it("returns to the graph and keeps the markets, range and evidence", () => {
    const inMarketView = {
      ...initialWorkspaceState,
      stage: "market-view" as const,
      ticker: "NVDA",
      selectedMarketIds: ["KXFED-26DEC"],
      evidence: [annotation("a")],
    };
    const next = workspaceReducer(inMarketView, { type: "BACK_TO_GRAPH" });
    expect(next).toEqual({ ...inMarketView, stage: "graph-ready" });
  });

  it("does nothing outside the market view", () => {
    expect(
      workspaceReducer(initialWorkspaceState, { type: "BACK_TO_GRAPH" }),
    ).toBe(initialWorkspaceState);
  });
});
