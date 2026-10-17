import type { CompanyGraph, GraphEdge, GraphNode, RelationshipType } from "../../../types/workspaceGraph";

/**
 * A company that is linked to one already on the graph but isn't on it yet:
 * what the "Add company" picker lists.
 */
export interface Candidate {
  /** The ticker, which is also the node id. */
  id: string;
  node: GraphNode;
  /** The on-graph company it is linked to. */
  via: string;
  /** The candidate's role for `via`: "supplier" means it supplies `via`. */
  role: RelationshipType;
  /** The edge to add with the node, exactly as the backend stated it. */
  edge: GraphEdge;
}

/** The relationship read from the other end: X's supplier is someone X is a customer of. */
export function reversed(relationship: RelationshipType): RelationshipType {
  if (relationship === "supplier") return "customer";
  if (relationship === "customer") return "supplier";
  return relationship;
}

/**
 * Companies linked to any on-graph company but not on the graph, from the
 * graphs fetched for those companies (`pool`: company id -> its own graph).
 * Each candidate appears once, through the first on-graph company whose
 * graph named it (the searched company's graph comes first, so a company
 * linked to it is described in relation to it). Insertion order of `pool`
 * is kept, so the list is stable as more graphs arrive.
 */
export function relatedCandidates(graph: CompanyGraph, pool: Map<string, CompanyGraph>): Candidate[] {
  const onGraph = new Set(graph.nodes.filter((n) => n.kind === "company").map((n) => n.id));
  const seen = new Set<string>();
  const out: Candidate[] = [];
  for (const [via, own] of pool) {
    if (!onGraph.has(via)) continue;
    const nodes = new Map(own.nodes.map((n) => [n.id, n]));
    for (const edge of own.edges) {
      if (edge.relationship === "board-interlock") continue;
      const otherId = edge.source === via ? edge.target : edge.target === via ? edge.source : null;
      if (otherId === null || onGraph.has(otherId) || seen.has(otherId)) continue;
      const node = nodes.get(otherId);
      if (!node || node.kind !== "company") continue;
      seen.add(otherId);
      out.push({
        id: otherId,
        node: { ...node, isCenter: false },
        via,
        role: edge.source === via ? edge.relationship : reversed(edge.relationship),
        edge,
      });
    }
  }
  return out;
}

/** The graph fragment that adds one candidate: its node, its edge, and any highlights about it. */
export function additionFor(candidate: Candidate, pool: Map<string, CompanyGraph>): CompanyGraph {
  const own = pool.get(candidate.via);
  return {
    nodes: [candidate.node],
    edges: [candidate.edge],
    highlights: (own?.highlights ?? []).filter((h) => h.target === candidate.id),
  };
}
