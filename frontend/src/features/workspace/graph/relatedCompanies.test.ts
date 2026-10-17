import { describe, expect, it } from "vitest";
import type { CompanyGraph } from "../../../types/workspaceGraph";
import { additionFor, relatedCandidates } from "./relatedCompanies";

const company = (id: string, isCenter = false) => ({ id, kind: "company" as const, label: id, name: `${id} Inc`, isCenter });

const graph: CompanyGraph = {
  nodes: [company("NVDA", true), company("TSM")],
  edges: [{ source: "NVDA", target: "TSM", relationship: "supplier" }],
};
// NVDA's own graph names TSM (on the graph) and MSFT (a customer: NVDA -> MSFT customer).
const nvda: CompanyGraph = {
  nodes: [company("NVDA", true), company("TSM"), company("MSFT")],
  edges: [
    { source: "NVDA", target: "TSM", relationship: "supplier" },
    { source: "NVDA", target: "MSFT", relationship: "customer" },
  ],
  highlights: [{ target: "MSFT", direction: "may_benefit", source_url: "u", title: "t" } as never],
};
// TSM's graph: ASML supplies TSM, and NVDA is a customer of TSM (stated from TSM's side).
const tsm: CompanyGraph = {
  nodes: [company("TSM", true), company("ASML"), company("NVDA"), company("MSFT")],
  edges: [
    { source: "TSM", target: "ASML", relationship: "supplier" },
    { source: "NVDA", target: "TSM", relationship: "supplier" },
    { source: "MSFT", target: "TSM", relationship: "supplier" }, // MSFT buys from TSM: MSFT is TSM's customer
  ],
};

describe("relatedCandidates", () => {
  it("lists off-graph companies once, described in relation to the first on-graph company that named them", () => {
    const pool = new Map([["NVDA", nvda], ["TSM", tsm]]);
    const candidates = relatedCandidates(graph, pool);
    expect(candidates.map((c) => [c.id, c.via, c.role])).toEqual([
      ["MSFT", "NVDA", "customer"],
      ["ASML", "TSM", "supplier"],
    ]);
    expect(candidates[0].node.isCenter).toBe(false);
  });

  it("reads an edge stated from the candidate's side the other way round", () => {
    const candidates = relatedCandidates({ ...graph, nodes: [company("NVDA", true), company("TSM"), company("ASML")] }, new Map([["TSM", tsm]]));
    expect(candidates.map((c) => [c.id, c.role])).toEqual([["MSFT", "customer"]]);
  });

  it("ignores graphs of companies no longer on the graph", () => {
    expect(relatedCandidates({ nodes: [company("NVDA", true)], edges: [] }, new Map([["TSM", tsm]]))).toEqual([]);
  });

  it("builds the addition from the candidate's node, edge and highlights", () => {
    const pool = new Map([["NVDA", nvda]]);
    const [msft] = relatedCandidates(graph, pool);
    const addition = additionFor(msft, pool);
    expect(addition.nodes.map((n) => n.id)).toEqual(["MSFT"]);
    expect(addition.edges).toEqual([{ source: "NVDA", target: "MSFT", relationship: "customer" }]);
    expect(addition.highlights).toHaveLength(1);
  });
});
