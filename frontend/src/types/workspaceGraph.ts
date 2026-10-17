import type { GraphHighlight } from "./graph";

/**
 * The company-to-company values match the backend's (company_graph/schemas.py),
 * so nothing has to be translated at the API boundary. "board-interlock" is
 * frontend-only: the backend's graph has no people in it.
 */
export type RelationshipType =
  | "supplier"
  | "customer"
  | "partner"
  | "competitor"
  | "sector_peer"
  | "board-interlock";

export type GraphNodeKind = "company" | "person";

export interface GraphNode {
  id: string;
  kind: GraphNodeKind;
  label: string;
  /** A company's full legal name; `label` is its ticker, which is what fits on the canvas. */
  name?: string;
  /** A company's industry, as SEC classifies it. */
  industry?: string;
  /** The ticker the user searched for — rendered with --accent, everything else neutral. */
  isCenter?: boolean;
  /** Only present on kind: "person" nodes (Stage 2 board-of-directors sub-network). */
  role?: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  relationship: RelationshipType;
  /** One sentence on what the relationship is, from the filing it was read in. */
  summary?: string;
  /** The filing that states the relationship. */
  evidenceUrl?: string;
}

export interface CompanyGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Recent events that may affect a linked company, as the backend returned them. */
  highlights?: GraphHighlight[];
}

// Relationship names used before the workspace adopted the backend's.
const LEGACY_RELATIONSHIPS: Record<string, RelationshipType> = {
  consumer: "customer",
  "industry-peer": "sector_peer",
};

/**
 * Projects saved before the rename still carry the old relationship names in
 * their graph snapshot. Rewrite them on load, so the rest of the app only
 * ever sees the current vocabulary.
 */
export function normalizeGraph(graph: CompanyGraph): CompanyGraph {
  if (!graph.edges.some((e) => e.relationship in LEGACY_RELATIONSHIPS)) return graph;
  return {
    ...graph,
    edges: graph.edges.map((e) => ({ ...e, relationship: LEGACY_RELATIONSHIPS[e.relationship] ?? e.relationship })),
  };
}
