import { describe, expect, it } from "vitest";
import type { CompanyGraph } from "../../types/workspaceGraph";
import { mergeGraphs } from "./graph";

const base: CompanyGraph = {
  nodes: [
    { id: "NVDA", kind: "company", label: "NVDA", isCenter: true },
    { id: "TSM", kind: "company", label: "TSM" },
  ],
  edges: [{ source: "NVDA", target: "TSM", relationship: "supplier" }],
  highlights: [],
};

// TSM's own graph: TSM is its center, NVDA shows up again, ASML is new.
const addition: CompanyGraph = {
  nodes: [
    { id: "TSM", kind: "company", label: "TSM", isCenter: true },
    { id: "NVDA", kind: "company", label: "NVDA" },
    { id: "ASML", kind: "company", label: "ASML" },
  ],
  edges: [
    { source: "TSM", target: "ASML", relationship: "supplier" },
    { source: "NVDA", target: "TSM", relationship: "supplier" }, // already known
  ],
  highlights: [],
};

describe("mergeGraphs", () => {
  it("adds new companies and edges, keeps one center, and dedupes known edges", () => {
    const merged = mergeGraphs(base, addition);
    expect(merged.nodes.map((n) => n.id)).toEqual(["NVDA", "TSM", "ASML"]);
    expect(merged.nodes.filter((n) => n.isCenter).map((n) => n.id)).toEqual([
      "NVDA",
    ]);
    expect(merged.edges).toHaveLength(2);
  });

  it("returns the same object when nothing is new", () => {
    expect(mergeGraphs(base, { nodes: base.nodes, edges: base.edges })).toBe(
      base,
    );
  });

  it("drops companies keyed by name rather than ticker, and their edges", () => {
    const merged = mergeGraphs(base, {
      nodes: [{ id: "Bitfarms Ltd", kind: "company", label: "Bitfarms Ltd" }],
      edges: [
        { source: "TSM", target: "Bitfarms Ltd", relationship: "competitor" },
      ],
    });
    expect(merged).toBe(base);
  });
});
