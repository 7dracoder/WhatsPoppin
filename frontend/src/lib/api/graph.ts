import {
  fetchGraph,
  GRAPH_FAKE,
  POLL_INTERVAL_MS,
} from "../../features/company-graph/api";
import type {
  BoardResponse,
  CompanyGraphResponse,
  GraphHighlight,
  GraphStatus,
} from "../../types/graph";
import type { CompanyGraph, GraphEdge } from "../../types/workspaceGraph";
import { ApiError, get } from "../apiClient";
import { delay } from "./mockUtils";

/**
 * Backend response (GET /graph/{ticker}) -> the workspace's graph shape.
 *
 * The backend keeps the searched company in its own `company` field and lists
 * only the *other* companies as nodes; here it becomes the center node. Nodes
 * are labelled by ticker — the backend's names are full legal names ("Taiwan
 * Semiconductor Manufacturing Company Limited"), too long to draw — with the
 * name kept alongside. Edge direction needs no change: in both shapes a
 * "supplier" edge means its target supplies its source.
 */
/**
 * The backend keys companies by ticker, except unlisted foreign ones, which it keys by name
 * ("Bitfarms Ltd"). Those can't be searched, charted or expanded, so the workspace leaves them out.
 */
export const TICKER_ID = /^[A-Z0-9]{1,6}([.-][A-Z0-9]{1,2})?$/;

function isTickerId(id: string): boolean {
  return TICKER_ID.test(id);
}

export function toWorkspaceGraph(response: CompanyGraphResponse): CompanyGraph {
  const { company } = response;
  const others = response.nodes.filter(
    (n) => n.symbol !== company.symbol && isTickerId(n.symbol),
  );
  const kept = new Set([company.symbol, ...others.map((n) => n.symbol)]);
  return {
    nodes: [
      {
        id: company.symbol,
        kind: "company",
        label: company.symbol,
        name: company.name,
        industry: company.industry ?? undefined,
        isCenter: true,
      },
      ...others.map((n) => ({
        id: n.symbol,
        kind: "company" as const,
        label: n.symbol,
        name: n.name,
        industry: n.industry ?? undefined,
      })),
    ],
    edges: response.links
      .filter((l) => kept.has(l.source) && kept.has(l.target))
      .map((l) => ({
        source: l.source,
        target: l.target,
        relationship: l.type,
        summary: l.summary,
        evidenceUrl: l.evidence_url,
      })),
    highlights: response.highlights,
  };
}

/**
 * Fold another company's graph into the one on screen, for the hop filter:
 * the backend only ever returns one company's direct links, so hop 2 is
 * "every hop-1 company's graph, merged". Nodes join by id (the addition's own
 * center becomes an ordinary node: there's one searched company), edges by
 * their endpoints and type, highlights by target and source URL. Returns
 * `base` itself when nothing new came in, so callers can skip a re-render.
 */
export function mergeGraphs(
  base: CompanyGraph,
  addition: CompanyGraph,
): CompanyGraph {
  const nodeIds = new Set(base.nodes.map((n) => n.id));
  const newNodes = addition.nodes
    .filter(
      (n) => !nodeIds.has(n.id) && (n.kind !== "company" || isTickerId(n.id)),
    )
    .map((n) => ({ ...n, isCenter: false }));
  const allIds = new Set([...nodeIds, ...newNodes.map((n) => n.id)]);
  const edgeKey = (e: GraphEdge) =>
    `${e.source}\u0000${e.target}\u0000${e.relationship}`;
  const edgeKeys = new Set(base.edges.map(edgeKey));
  const newEdges = addition.edges.filter(
    (e) =>
      !edgeKeys.has(edgeKey(e)) && allIds.has(e.source) && allIds.has(e.target),
  );
  const highlightKey = (h: GraphHighlight) =>
    `${h.target}\u0000${h.source_url}`;
  const highlightKeys = new Set((base.highlights ?? []).map(highlightKey));
  const newHighlights = (addition.highlights ?? []).filter(
    (h) => !highlightKeys.has(highlightKey(h)),
  );
  if (
    newNodes.length === 0 &&
    newEdges.length === 0 &&
    newHighlights.length === 0
  )
    return base;
  return {
    nodes: [...base.nodes, ...newNodes],
    edges: [...base.edges, ...newEdges],
    highlights: [...(base.highlights ?? []), ...newHighlights],
  };
}

export interface GraphUpdate {
  graph: CompanyGraph;
  /** "running" means more links may still arrive; "error" can still come with partial links. */
  status: GraphStatus;
  /**
   * True once every link is in. The backend keeps answering "running" while
   * it then checks recent news for the graph's companies, which adds
   * highlights but no nodes; that can take minutes and isn't "loading".
   */
  linksDone: boolean;
}

/** Why a graph couldn't be fetched at all — the two cases read differently to the user. */
export type GraphFailure = "unknown-ticker" | "unavailable";

/**
 * GET /graph/{ticker}, or the local fixtures with VITE_GRAPH_FAKE=1 (shared
 * with the Company Graph page, so the workspace also runs with no backend).
 * `attempt` counts polls for this search; fake mode uses it to imitate a
 * build in progress.
 */
export async function fetchCompanyGraph(
  ticker: string,
  attempt = 0,
  linksOnly = false,
): Promise<GraphUpdate> {
  const symbol = ticker.trim().replace(/^\$/, "").toUpperCase();
  const response = await fetchGraph(symbol, attempt, linksOnly);
  return {
    graph: toWorkspaceGraph(response),
    status: response.status,
    // An older backend sends no links_status: fall back to the overall status.
    linksDone: (response.links_status ?? response.status) !== "running",
  };
}

/**
 * Fetches a company's graph and keeps polling while the backend is still
 * building it. The backend answers at once with whatever links it has stored
 * and status "running", so `onUpdate` fires once per response — each a fuller
 * graph than the last — until "done" or "error". Returns a function that
 * stops the polling.
 */
// A full link run (SEC + the model) takes a minute or two; this is ~5 min at POLL_INTERVAL_MS.
const GRAPH_MAX_POLLS = 200;
// Once the links are in, only highlights can still change, and the news run behind them takes
// minutes: poll slowly, and for at most ~10 min.
const NEWS_POLL_INTERVAL_MS = 5000;
const NEWS_MAX_POLLS = 120;

export function pollCompanyGraph(
  ticker: string,
  handlers: {
    onUpdate: (update: GraphUpdate) => void;
    onFailure: (reason: GraphFailure) => void;
  },
  /** Skip the news run: for hop expansion, which only needs each company's links. */
  linksOnly = false,
): () => void {
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  // Set once a response says every link is in. From then on the graph on screen is complete,
  // so a failed or abandoned poll is not a failure to load it: the polling just stops.
  let linksDone = false;
  let newsPolls = 0;

  const poll = async (attempt: number) => {
    try {
      const update = await fetchCompanyGraph(ticker, attempt, linksOnly);
      if (cancelled) return;
      linksDone ||= update.linksDone;
      handlers.onUpdate(update);
      if (update.status !== "running") return;
      if (linksDone) {
        newsPolls += 1;
        if (newsPolls >= NEWS_MAX_POLLS) return;
        timer = setTimeout(() => void poll(attempt + 1), NEWS_POLL_INTERVAL_MS);
        return;
      }
      if (attempt + 1 >= GRAPH_MAX_POLLS) {
        // A build stuck in "running" (a crashed backend task) shouldn't be polled forever.
        handlers.onFailure("unavailable");
        return;
      }
      timer = setTimeout(() => void poll(attempt + 1), POLL_INTERVAL_MS);
    } catch (err) {
      if (cancelled || linksDone) return;
      // 404 is the backend saying the ticker isn't a listed company; anything
      // else (503, a network failure) is the service itself being unreachable.
      handlers.onFailure(
        err instanceof ApiError && err.status === 404
          ? "unknown-ticker"
          : "unavailable",
      );
    }
  };
  void poll(0);

  return () => {
    cancelled = true;
    if (timer) clearTimeout(timer);
  };
}

// The backend gives a board build 60 s; stop asking a little after that so a
// stuck run can't hold up the people reveal forever.
const BOARD_MAX_POLLS = 50;

// Fake-mode boards: copies of the backend's company_graph/board_fixtures/
// (a backend test fails if the two drift apart). Every person in them is
// invented — made-up data shouldn't attribute board seats to real people.
// Loaded eagerly at build time; the files are a few KB in total.
const BOARD_FIXTURES = import.meta.glob<BoardResponse>(
  "../../features/company-graph/board-fixtures/*.json",
  {
    eager: true,
    import: "default",
  },
);

/** The fixture board for a ticker; like the backend's fake mode, an unknown ticker has an empty one. */
async function fakeBoard(ticker: string): Promise<BoardResponse> {
  const path = Object.keys(BOARD_FIXTURES).find((p) =>
    p.endsWith(`/${ticker.toUpperCase()}.json`),
  );
  const empty: BoardResponse = {
    company: { symbol: ticker, name: ticker },
    status: "done",
    members: [],
  };
  return delay(path ? BOARD_FIXTURES[path] : empty, 150);
}

/**
 * A company's directors as a small graph: the company plus one person node
 * per director, joined by "board-interlock" edges.
 *
 * Real mode calls GET /graph/{ticker}/board, which answers at once and builds
 * in the background like the links endpoint, so this polls while it says
 * "running" and resolves with the finished board. With VITE_GRAPH_FAKE=1 it
 * reads the fixtures above instead. Either way person ids are SEC person ids,
 * which is what merges a director who sits on two boards into a single node.
 */
export async function fetchBoardNetwork(
  companyId: string,
): Promise<CompanyGraph> {
  const load = () =>
    GRAPH_FAKE
      ? fakeBoard(companyId)
      : get<BoardResponse>(`/graph/${encodeURIComponent(companyId)}/board`);

  let board = await load();
  for (
    let polls = 1;
    board.status === "running" && polls < BOARD_MAX_POLLS;
    polls++
  ) {
    await delay(undefined, POLL_INTERVAL_MS);
    board = await load();
  }
  return {
    nodes: [
      { id: companyId, kind: "company", label: companyId, isCenter: true },
      ...board.members.map((m) => ({
        id: m.id,
        kind: "person" as const,
        label: m.name,
        role: m.role,
      })),
    ],
    edges: board.members.map((m) => ({
      source: companyId,
      target: m.id,
      relationship: "board-interlock" as const,
      evidenceUrl: m.evidence_url,
    })),
  };
}
