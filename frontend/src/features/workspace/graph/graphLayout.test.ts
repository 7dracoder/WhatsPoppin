import { describe, expect, it } from "vitest";
import type { GraphEdge, GraphNode, RelationshipType } from "../../../types/workspaceGraph";
import { computeGraphLayout, initials } from "./graphLayout";

const company = (id: string, isCenter = false): GraphNode => ({ id, label: id, kind: "company", isCenter });

/** A center "C" with `count` companies of one relationship, named A0, A1, ... */
function star(relationship: RelationshipType, count: number) {
  const ids = Array.from({ length: count }, (_, i) => `A${i}`);
  const edges = ids.map((id): GraphEdge => ({ source: "C", target: id, relationship }) as GraphEdge);
  const layout = computeGraphLayout([company("C", true), ...ids.map((id) => company(id))], edges, []);
  return ids.map((id) => layout.targets.get(id)!);
}

const distinct = (values: number[]) => [...new Set(values)].sort((a, b) => a - b);

describe("computeGraphLayout", () => {
  it("keeps a small tier on one line above the center", () => {
    const points = star("supplier", 4);
    expect(distinct(points.map((p) => p.y))).toHaveLength(1);
    expect(points[0].y).toBeLessThan(0);
  });

  it("wraps a large tier onto balanced, staggered lines instead of one long row", () => {
    const points = star("supplier", 8);
    const ys = distinct(points.map((p) => p.y));
    expect(ys).toHaveLength(2);
    const [far, near] = ys.map((y) => points.filter((p) => p.y === y));
    expect([far.length, near.length]).toEqual([4, 4]);
    // No company on the far line sits straight behind one on the near line.
    expect(far.some((f) => near.some((n) => n.x === f.x))).toBe(false);
    const width = (line: typeof near) => Math.max(...line.map((p) => p.x)) - Math.min(...line.map((p) => p.x));
    expect(width(near)).toBeLessThan(width(star("supplier", 5)) + 1); // never wider than a full single line
  });

  it("fans peers out beside the center, above and below its line", () => {
    const points = star("competitor", 10);
    expect(points.every((p) => p.x !== 0)).toBe(true); // never stacked on the center's column
    expect(distinct(points.map((p) => p.y)).length).toBeGreaterThan(1);
    const perLine = distinct(points.map((p) => p.y)).map((y) => points.filter((p) => p.y === y && p.x > 0).length);
    expect(Math.max(...perLine)).toBeLessThanOrEqual(2);
  });

  it("puts suppliers above a wrapped peer block rather than through it", () => {
    const peers = Array.from({ length: 10 }, (_, i) => `P${i}`);
    const edges = [
      ...peers.map((id): GraphEdge => ({ source: "C", target: id, relationship: "competitor" }) as GraphEdge),
      { source: "C", target: "S", relationship: "supplier" } as GraphEdge,
    ];
    const layout = computeGraphLayout([company("C", true), company("S"), ...peers.map((id) => company(id))], edges, []);
    const topPeer = Math.min(...peers.map((id) => layout.targets.get(id)!.y));
    expect(layout.targets.get("S")!.y).toBeLessThan(topPeer);
  });
});

describe("board rings", () => {
  it("spaces a board evenly around its company at one radius", () => {
    const people = Array.from({ length: 6 }, (_, i) => `P${i}`);
    const layout = computeGraphLayout(
      [company("C", true)],
      [],
      people.map((personId) => ({ companyId: "C", personId })),
    );
    const radii = people.map((id) => Math.hypot(layout.targets.get(id)!.x, layout.targets.get(id)!.y));
    expect(Math.max(...radii) - Math.min(...radii)).toBeLessThan(1e-9);
    const angles = people.map((id) => Math.atan2(layout.targets.get(id)!.y, layout.targets.get(id)!.x)).sort((a, b) => a - b);
    const gaps = angles.map((a, i) => (i === 0 ? a + 2 * Math.PI - angles[angles.length - 1] : a - angles[i - 1]));
    expect(Math.max(...gaps) - Math.min(...gaps)).toBeLessThan(1e-9);
    // A ring reaches both sides, so the next row's company sits past two rings.
    const two = computeGraphLayout(
      [company("C", true), company("S")],
      [{ source: "C", target: "S", relationship: "supplier" } as GraphEdge],
      people.map((personId) => ({ companyId: "C", personId })),
    );
    expect(Math.abs(two.targets.get("S")!.y)).toBeGreaterThan(2 * radii[0]);
  });
});

describe("initials", () => {
  it("takes the first letter of the first and last name", () => {
    expect(initials("Robyn M Denholm")).toBe("RD");
    expect(initials("Jensen Huang")).toBe("JH");
  });
  it("ignores suffixes and punctuation, and copes with one word or none", () => {
    expect(initials("John A. Smith Jr.")).toBe("JS");
    expect(initials("Vanguard Group Inc")).toBe("VI");
    expect(initials("Madonna")).toBe("M");
    expect(initials("")).toBe("");
  });
});
