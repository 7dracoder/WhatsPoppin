import type { ChartRange } from "./market";
import type { ContentItem, Sentiment } from "./content";
import type { CompanyGraph } from "./workspaceGraph";

export type WorkspaceStage =
  | "empty"
  | "building-graph"
  | "graph-ready"
  | "generating-tags"
  | "picking-markets"
  | "market-view";

export type AsyncStatus = "idle" | "loading" | "error" | "done";

export interface EvidenceAnnotation {
  id: string;
  item: ContentItem;
  /** Resolved once from item.published_at so chart code doesn't re-parse repeatedly. */
  timestamp: string;
  /** Jev's sentiment for the item when it was added; absent if it hadn't been classified yet. */
  sentiment?: Sentiment;
}

export interface QueryResult {
  id: string;
  query: string;
  label: string;
  percentage: number;
  n: number;
  createdAt: string;
}

export interface WorkspaceState {
  stage: WorkspaceStage;
  ticker: string | null;
  graph: CompanyGraph | null;
  graphStatus: AsyncStatus;
  /** The graph's links are all in, and the backend is still checking recent news for highlights. */
  graphNewsRunning: boolean;
  tags: string[];
  tagsStatus: AsyncStatus;
  suggestedMarkets: import("./market").MarketCard[];
  marketsStatus: AsyncStatus;
  selectedMarketIds: string[];
  range: ChartRange;
  /**
   * Where the chart's window ends, as a calendar day ("2026-10-09"), or null
   * for "now". Set by stepping back a day (arrows or dragging the chart).
   * Not saved with a project: it's a viewing position, not part of the analysis.
   */
  chartDay: string | null;
  evidence: EvidenceAnnotation[];
  pinnedAnnotationId: string | null;
  queryResults: QueryResult[];
  activeProjectId: string | null;
  activeProjectName: string | null;
  /**
   * Bumped whenever the graph is *replaced* (new search, project opened, reset),
   * as opposed to grown by a polled update. The canvas restarts its build-out
   * animation on this, not on `graph` changing identity.
   */
  graphSession: number;
  /** Bumped on every save so the sidebar refetches even when the active id didn't change. */
  projectsRevision: number;
}

/** The editable fields — what POST/PUT /projects accept. */
export interface ProjectInput {
  name: string;
  /** Null for a project created from the sidebar's "+" before any graph has been built. */
  ticker: string | null;
  /** Frozen at save time — reopening a project never re-triggers the build animation or re-fetches. */
  graphSnapshot: CompanyGraph | null;
  selectedMarketIds: string[];
  suggestedMarkets: import("./market").MarketCard[];
  evidence: EvidenceAnnotation[];
  /** Optional so projects saved before the range selector still load (default "1m"). */
  range?: ChartRange;
}

/** A persisted project as returned by the backend (backend/api/projects.py). */
export interface Project extends ProjectInput {
  id: string;
  createdAt: string;
  updatedAt: string;
}

export const initialWorkspaceState: WorkspaceState = {
  stage: "empty",
  ticker: null,
  graph: null,
  graphStatus: "idle",
  graphNewsRunning: false,
  tags: [],
  tagsStatus: "idle",
  suggestedMarkets: [],
  marketsStatus: "idle",
  selectedMarketIds: [],
  range: "1m",
  chartDay: null,
  evidence: [],
  pinnedAnnotationId: null,
  queryResults: [],
  activeProjectId: null,
  activeProjectName: null,
  graphSession: 0,
  projectsRevision: 0,
};
