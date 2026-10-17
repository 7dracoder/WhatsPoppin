import { describe, expect, it } from "vitest";
import { initialWorkspaceState } from "../../types/project";
import { spokenName, workspaceContext } from "./useWorkspaceAssistant";

describe("spokenName", () => {
  it("drops legal suffixes and un-shouts SEC names", () => {
    expect(spokenName("NVIDIA CORP")).toBe("Nvidia");
    expect(spokenName("COHERENT CORP.")).toBe("Coherent");
    expect(spokenName("MICRON TECHNOLOGY INC")).toBe("Micron Technology");
    expect(spokenName("Fabrinet")).toBe("Fabrinet");
    expect(spokenName("Tesla, Inc.")).toBe("Tesla");
  });
});

describe("workspaceContext", () => {
  it("describes the graph the way the screen shows it", () => {
    const ctx = workspaceContext({
      ...initialWorkspaceState,
      stage: "graph-ready",
      ticker: "NVDA",
      graph: {
        nodes: [
          { id: "NVDA", kind: "company", label: "NVDA", name: "NVIDIA CORP", isCenter: true },
          { id: "TSM", kind: "company", label: "TSM", name: "Taiwan Semiconductor" },
        ],
        edges: [{ source: "NVDA", target: "TSM", relationship: "supplier", summary: "Makes its GPUs." }],
      },
    });
    expect(ctx.company).toEqual({ ticker: "NVDA", name: "NVIDIA CORP", industry: null });
    expect(ctx.linked_companies).toEqual([
      { ticker: "TSM", name: "Taiwan Semiconductor", relationship: "supplier", summary: "Makes its GPUs." },
    ]);
  });
});
