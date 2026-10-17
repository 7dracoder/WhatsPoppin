import type { GraphStatus } from "../../types/graph";
import { mergeGraphs } from "../../lib/api/graph";
import { normalizeGraph, type CompanyGraph } from "../../types/workspaceGraph";
import type { ChartRange, MarketCard } from "../../types/market";
import type {
  AsyncStatus,
  EvidenceAnnotation,
  Project,
  QueryResult,
  WorkspaceState,
} from "../../types/project";
import { initialWorkspaceState } from "../../types/project";

export type WorkspaceAction =
  | { type: "TICKER_SUBMITTED"; ticker: string }
  | { type: "TICKER_CLEARED" } // back to the ticker prompt after a search that found nothing
  | { type: "GRAPH_STATUS"; status: AsyncStatus }
  // One per backend response: `status` "running" means more links are still coming.
  | {
      type: "GRAPH_LOADED";
      ticker: string;
      graph: CompanyGraph;
      status: GraphStatus;
      /** Every link is in; a "running" status now only means the news run. */
      linksDone: boolean;
    }
  | { type: "GRAPH_READY" } // graph complete and every company revealed
  | { type: "GRAPH_EXPANDED"; ticker: string; addition: CompanyGraph } // hop filter: a linked company's graph to merge in
  | { type: "TAGS_STATUS"; status: AsyncStatus }
  | { type: "TAGS_LOADED"; tags: string[] }
  | { type: "MARKETS_STATUS"; status: AsyncStatus }
  | { type: "MARKETS_LOADED"; markets: MarketCard[] }
  | { type: "MARKET_TOGGLED"; marketId: string }
  | { type: "MARKET_ADDED"; market: MarketCard }
  | { type: "MARKET_SWAPPED"; marketId: string; market: MarketCard } // another outcome of the same event takes this market's place
  | { type: "RANGE_CHANGED"; range: ChartRange }
  | { type: "CHART_DAY_SHIFTED"; days: number } // +1 / -1 calendar day; forward of "now" clamps back to null
  | { type: "CHART_DAY_RESET" } // back to "now"
  | { type: "GENERATE_PRESSED" } // Stage 5 — slide transition into market-view
  | { type: "BACK_TO_GRAPH" } // market-view -> the graph, keeping everything picked so far
  | { type: "EVIDENCE_ADDED"; annotation: EvidenceAnnotation }
  | { type: "EVIDENCE_REMOVED"; itemId: string } // by the tweet/article id, which is all the picker knows
  | { type: "EVIDENCE_PINNED"; annotationId: string | null }
  | { type: "QUERY_RESULT_ADDED"; result: QueryResult }
  | { type: "PROJECT_LOADED"; project: Project }
  | { type: "PROJECT_SAVED"; id: string; name: string }
  | { type: "RESET" };

export function workspaceReducer(
  state: WorkspaceState,
  action: WorkspaceAction,
): WorkspaceState {
  switch (action.type) {
    case "TICKER_SUBMITTED":
      // Keep the active project: a project added empty from the sidebar gets its
      // ticker here, and saving afterwards should update it rather than fork a new one.
      return {
        ...initialWorkspaceState,
        ticker: action.ticker,
        stage: "building-graph",
        graphStatus: "loading",
        graphSession: state.graphSession + 1,
        activeProjectId: state.activeProjectId,
        activeProjectName: state.activeProjectName,
      };

    case "TICKER_CLEARED":
      // Like TICKER_SUBMITTED, this keeps the active project attached.
      return {
        ...initialWorkspaceState,
        graphSession: state.graphSession + 1,
        activeProjectId: state.activeProjectId,
        activeProjectName: state.activeProjectName,
        projectsRevision: state.projectsRevision,
      };

    case "GRAPH_STATUS":
      return { ...state, graphStatus: action.status };

    case "GRAPH_LOADED": {
      // A response for a search the user has since moved on from — a poll
      // that was in flight when they opened a project or searched again.
      if (action.ticker !== state.ticker || state.stage === "market-view")
        return state;
      const graphNewsRunning = action.status === "running" && action.linksDone;
      // The links were already complete: this response can only bring highlights. Keep the graph
      // in state, which may hold hop expansions merged in since, and take just the highlights.
      if (state.graph && state.graphStatus !== "loading")
        return {
          ...state,
          graph: { ...state.graph, highlights: action.graph.highlights },
          graphNewsRunning,
        };
      // "running" is only "loading" while links can still arrive. The news run that follows
      // takes minutes and adds no companies, so the graph is done as soon as its links are.
      const graphStatus =
        action.status !== "running"
          ? action.status
          : action.linksDone
            ? "done"
            : "loading";
      return { ...state, graph: action.graph, graphStatus, graphNewsRunning };
    }

    case "GRAPH_EXPANDED": {
      // Same staleness rule as GRAPH_LOADED. Merged here, against the graph in state, so two
      // expansions finishing close together never overwrite each other (a merge computed by the
      // caller against an older graph would drop the other's nodes).
      if (
        action.ticker !== state.ticker ||
        state.stage === "market-view" ||
        !state.graph
      )
        return state;
      const merged = mergeGraphs(state.graph, action.addition);
      return merged === state.graph ? state : { ...state, graph: merged };
    }

    case "GRAPH_READY":
      // Only ever a step forward from the build-out: tag generation may
      // already have moved the stage on by the time the last node lands.
      return state.stage === "building-graph"
        ? { ...state, stage: "graph-ready" }
        : state;

    case "TAGS_STATUS":
      return { ...state, stage: "generating-tags", tagsStatus: action.status };

    case "TAGS_LOADED":
      return {
        ...state,
        tags: action.tags,
        tagsStatus: "done",
        stage: "picking-markets",
      };

    case "MARKETS_STATUS":
      return { ...state, marketsStatus: action.status };

    case "MARKETS_LOADED":
      return {
        ...state,
        suggestedMarkets: action.markets,
        marketsStatus: "done",
      };

    case "MARKET_TOGGLED": {
      const already = state.selectedMarketIds.includes(action.marketId);
      return {
        ...state,
        selectedMarketIds: already
          ? state.selectedMarketIds.filter((id) => id !== action.marketId)
          : [...state.selectedMarketIds, action.marketId],
      };
    }

    case "MARKET_SWAPPED": {
      // In place, so the chart keeps the series' color and position: a swap is "show me the
      // TSM answer instead of the AI answer", not a new selection. Swapping to a market that
      // is already selected just drops the old one, rather than listing it twice.
      const { marketId, market } = action;
      if (marketId === market.marketId) return state;
      const alreadyThere = state.selectedMarketIds.includes(market.marketId);
      return {
        ...state,
        suggestedMarkets: state.suggestedMarkets.some((m) => m.marketId === market.marketId)
          ? state.suggestedMarkets.filter((m) => m.marketId !== marketId)
          : state.suggestedMarkets.map((m) => (m.marketId === marketId ? market : m)),
        selectedMarketIds: alreadyThere
          ? state.selectedMarketIds.filter((id) => id !== marketId)
          : state.selectedMarketIds.map((id) => (id === marketId ? market.marketId : id)),
      };
    }

    case "MARKET_ADDED":
      return {
        ...state,
        suggestedMarkets: state.suggestedMarkets.some(
          (m) => m.marketId === action.market.marketId,
        )
          ? state.suggestedMarkets
          : [...state.suggestedMarkets, action.market],
        selectedMarketIds: state.selectedMarketIds.includes(
          action.market.marketId,
        )
          ? state.selectedMarketIds
          : [...state.selectedMarketIds, action.market.marketId],
      };

    case "RANGE_CHANGED":
      return { ...state, range: action.range };

    case "CHART_DAY_SHIFTED":
      return { ...state, chartDay: shiftDay(state.chartDay, action.days) };

    case "CHART_DAY_RESET":
      return state.chartDay === null ? state : { ...state, chartDay: null };

    case "GENERATE_PRESSED":
      return { ...state, stage: "market-view" };

    case "BACK_TO_GRAPH":
      // Only the stage changes: the selected markets, range and evidence stay, so Generate
      // returns to the same chart. "graph-ready" because the graph on screen is already built.
      return state.stage === "market-view"
        ? { ...state, stage: "graph-ready" }
        : state;

    case "EVIDENCE_ADDED":
      if (state.evidence.some((e) => e.id === action.annotation.id))
        return state;
      return { ...state, evidence: [...state.evidence, action.annotation] };

    case "EVIDENCE_REMOVED": {
      const removed = state.evidence.filter((e) => e.item.id === action.itemId);
      if (removed.length === 0) return state;
      return {
        ...state,
        evidence: state.evidence.filter((e) => e.item.id !== action.itemId),
        // Don't leave the drawer pointing at evidence that no longer exists.
        pinnedAnnotationId: removed.some(
          (e) => e.id === state.pinnedAnnotationId,
        )
          ? null
          : state.pinnedAnnotationId,
      };
    }

    case "EVIDENCE_PINNED":
      return { ...state, pinnedAnnotationId: action.annotationId };

    case "QUERY_RESULT_ADDED":
      return { ...state, queryResults: [action.result, ...state.queryResults] };

    case "PROJECT_LOADED":
      if (!action.project.graphSnapshot) {
        // Nothing built yet — start at the ticker prompt, attached to this project.
        return {
          ...initialWorkspaceState,
          graphSession: state.graphSession + 1,
          activeProjectId: action.project.id,
          activeProjectName: action.project.name,
        };
      }
      return {
        ...initialWorkspaceState,
        stage: "market-view",
        ticker: action.project.ticker,
        // Snapshots saved before the relationship rename carry the old names.
        graph: normalizeGraph(action.project.graphSnapshot),
        graphStatus: "done",
        graphSession: state.graphSession + 1,
        suggestedMarkets: action.project.suggestedMarkets,
        marketsStatus: "done",
        selectedMarketIds: action.project.selectedMarketIds,
        // Projects saved before the history route stored "3m", which no longer exists.
        range:
          (action.project.range as string) === "3m"
            ? "6m"
            : (action.project.range ?? "1m"),
        evidence: action.project.evidence,
        activeProjectId: action.project.id,
        activeProjectName: action.project.name,
      };

    case "PROJECT_SAVED":
      return {
        ...state,
        activeProjectId: action.id,
        activeProjectName: action.name,
        projectsRevision: state.projectsRevision + 1,
      };

    case "RESET":
      return { ...initialWorkspaceState, graphSession: state.graphSession + 1 };

    default:
      return state;
  }
}

/** Today's date as "YYYY-MM-DD" in UTC, the calendar the backend's windows use. */
export function todayUtc(now: number = Date.now()): string {
  return new Date(now).toISOString().slice(0, 10);
}

/**
 * Move the chart's day cursor. From "now" (null) a step back lands on
 * yesterday; stepping forward onto or past today returns to "now" (null), so
 * the chart never asks for a window in the future.
 */
export function shiftDay(
  day: string | null,
  days: number,
  now: number = Date.now(),
): string | null {
  const today = todayUtc(now);
  const from = day ?? today;
  const next = new Date(`${from}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + days);
  const result = next.toISOString().slice(0, 10);
  return result >= today ? null : result;
}
