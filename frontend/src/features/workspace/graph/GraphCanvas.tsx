import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, {
  type ForceGraphMethods,
  type NodeObject,
} from "react-force-graph-2d";
import {
  fetchBoardNetwork,
  pollCompanyGraph,
  type GraphFailure,
} from "../../../lib/api/graph";
import type {
  CompanyGraph,
  GraphEdge,
  GraphNode,
} from "../../../types/workspaceGraph";
import { useWorkspace } from "../useWorkspace";
import { useTheme } from "../../theme/useTheme";
import { latestHighlightBySymbol } from "../../company-graph/highlights";
import { DIRECTION_TOKEN } from "../../company-graph/labels";
import { GraphLegend, RELATIONSHIP_COLORS } from "./GraphLegend";
import { useLiveAlerts } from "../alerts/LiveAlertsProvider";
import { AddCompanyPicker } from "./AddCompanyPicker";
import { NodeInfoCard } from "./NodeInfoCard";
import { additionFor, relatedCandidates, type Candidate } from "./relatedCompanies";
import {
  computeGraphLayout,
  moveCompany,
  targetForce,
  type GraphLayout,
  type Point,
  initials,
} from "./graphLayout";

/** Reads a theme CSS variable at draw time so canvas-rendered nodes stay in sync
 * with the active [data-theme]/[data-mode] — canvas can't consume var() directly. */
function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return value || fallback;
}

const REVEAL_STAGGER_MS = 90;
const SETTLE_PAUSE_MS = 200;
// People reveal one at a time like the companies do, but faster — there are
// ~60 of them across the mock boards, so at the company stagger they'd take
// several seconds to finish.
const PERSON_STAGGER_MS = 35;
// The camera stays put during the build-out and fits exactly once, after the
// last person lands — slow and generously padded so the whole graph settles
// into view with real breathing room around the edges.
const ZOOM_FIT_FINAL_MS = 1100;
const ZOOM_FIT_FINAL_PADDING = 96;
// Person names draw only once the user has zoomed in this many times past
// the final fitted zoom. Relative, not absolute: an absolute threshold (the
// old 2.2) is meaningless when zoomToFit's own resulting scale depends on how
// spread out the graph is — a compact graph could fit at 2.5x and show every
// name before the user had zoomed at all.
const PERSON_LABEL_ZOOM_MULTIPLIER = 1.35;

type PositionedNode = GraphNode & { x?: number; y?: number };

/** Drawn radius of a node, in graph units. Shared by the painter and the hit area. */
function nodeRadius(node: GraphNode): number {
  if (node.isCenter) return 8.5;
  return node.kind === "person" ? 3.5 : 6;
}

// Size of the direction arrow on supplier/customer edges, in graph units.
const ARROW_LENGTH = 9;
const ARROW_HALF_WIDTH = 4.5;

type VisibleGraph = { nodes: GraphNode[]; edges: GraphEdge[] };
/** One board seat: a person and the company -> person edge that seats them. */
type Seat = { person: GraphNode; edge: GraphEdge };

const EMPTY_LAYOUT: GraphLayout = {
  targets: new Map(),
  personAnchors: new Map(),
  labelAbove: new Set(),
};

// The simulation swaps an edge's id strings for live node objects once the
// edge is in play, so an edge's ends have to be read through this.
function endId(end: unknown): string {
  return typeof end === "object" && end !== null
    ? (end as GraphNode).id
    : String(end);
}

function edgeKey(e: GraphEdge): string {
  return `${endId(e.source)}|${endId(e.target)}|${e.relationship}`;
}

/**
 * Brings the drawn company edges in line with `graph` for the nodes on
 * screen: adds the ones whose two companies are both visible now, drops the
 * ones the graph no longer has. Existing edge objects are reused, never
 * rebuilt — the simulation holds on to them — and new ones are cloned, since
 * it rewrites their source/target in place and `graph.edges` has to keep its
 * id strings. Board edges aren't in `graph` and pass through untouched.
 */
function syncEdges(
  nodes: GraphNode[],
  edges: GraphEdge[],
  graph: CompanyGraph,
): GraphEdge[] {
  const ids = new Set(nodes.map((n) => n.id));
  const wanted = new Map(
    graph.edges
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((e) => [edgeKey(e), e] as const),
  );
  const kept = edges.filter((e) =>
    e.relationship === "board-interlock"
      ? ids.has(endId(e.source)) && ids.has(endId(e.target))
      : wanted.has(edgeKey(e)),
  );
  const have = new Set(kept.map(edgeKey));
  const added = [...wanted]
    .filter(([key]) => !have.has(key))
    .map(([, e]) => ({ ...e }));
  return added.length === 0 && kept.length === edges.length
    ? edges
    : [...kept, ...added];
}

// How hard each node is pulled toward its slot in the layout per tick.
// Low enough that nodes visibly glide out from where they spawn instead of
// snapping into place.
const LAYOUT_PULL = 0.12;

/**
 * Stage 0's full-bleed canvas background, and Stages 1-2's graph render.
 * Self-gates on state.stage/state.graph so the parent page can mount it
 * unconditionally alongside <TickerInput /> (per the barrel's contract).
 */
/** On-graph companies whose own graphs are fetched at once for the "Add company" list. */
const EXPAND_CONCURRENCY = 2;

export function GraphCanvas() {
  const { state, dispatch } = useWorkspace();
  const { mode, theme } = useTheme();
  const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const fgRef = useRef<
    ForceGraphMethods<NodeObject<GraphNode>, GraphEdge> | undefined
  >(undefined);
  const [visible, setVisible] = useState<VisibleGraph>({
    nodes: [],
    edges: [],
  });
  // Set when the graph couldn't be fetched (as opposed to the backend
  // reporting its own build as failed, which arrives as a normal response).
  const [graphFailure, setGraphFailure] = useState<GraphFailure | null>(null);

  // Bookkeeping for one build-out (see the effects below). All refs: none of
  // it is rendered, and timers and promise callbacks need the live values.
  const buildRef = useRef({
    /** Identity of the current build-out; async callbacks compare against it to detect they're stale. */
    token: {},
    /** The latest graph, for callbacks that outlive the render that scheduled them. */
    graph: null as CompanyGraph | null,
    /** Companies already scheduled to appear. */
    queued: new Set<string>(),
    /** When the next queued company may appear (performance.now() time), to keep the stagger. */
    nextRevealAt: 0,
    /** One in-flight or settled board fetch per company. */
    boards: new Map<string, Promise<void>>(),
    seats: [] as Seat[],
    /** The graph is complete and the people reveal has been scheduled. */
    finishing: false,
    /**
     * The first people reveal has run. Before it, a board that arrives just
     * adds its seats and waits for that reveal; after it (boards of companies
     * the hop slider brought in), a board reveals its own seats on arrival.
     */
    peopleRevealed: false,
    timers: [] as number[],
  });
  // Zoom scale right after the final fit — the baseline person labels are
  // measured against. Null until then, which keeps names hidden throughout
  // the build-out.
  const fitZoomRef = useRef<number | null>(null);
  // Target positions for every node (see graphLayout.ts). A ref, read by the
  // custom force on every tick, so recomputing it when boards arrive moves
  // nodes without re-registering anything.
  const layoutRef = useRef<GraphLayout>(EMPTY_LAYOUT);
  // Where the user has dropped each company they dragged. The node itself
  // stays put through its own fx/fy; this is kept so the move can be
  // re-applied to a layout that gets recomputed afterwards.
  const movedCompaniesRef = useRef(new Map<string, Point>());
  // The node whose details card is open, by id — the node objects themselves
  // are mutated by the simulation, so they make poor state.
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Memoized so an unrelated re-render doesn't hand ForceGraph2D a new
  // graphData object — it compares by identity and reheats the simulation
  // on every new one.
  const graphData = useMemo(
    () => ({ nodes: visible.nodes, links: visible.edges }),
    [visible],
  );

  // The newest highlight per company: a company a recent event may affect is
  // drawn in that highlight's direction color, so it stands out before it's clicked.
  const highlightBySymbol = useMemo(
    () => latestHighlightBySymbol(state.graph?.highlights ?? []),
    [state.graph],
  );
  // Companies a large live market move touched in the last hour flash green
  // (Yes up) or red (Yes down). The color is the market's direction, not a
  // call on the stock. Read through a ref inside the draw callback so each
  // poll doesn't hand ForceGraph2D a new callback and reheat the simulation.
  const { flashBySymbol } = useLiveAlerts();
  const flashRef = useRef(flashBySymbol);
  flashRef.current = flashBySymbol;
  // Only a live market move animates (news highlights are a steady color), so
  // the canvas keeps redrawing just while one is on screen.
  const hasFlashes = visible.nodes.some((n) => flashBySymbol.has(n.id));

  // The "Add company" list. The backend returns one company's direct links,
  // so companies one step further out come from fetching each on-graph
  // company's own graph (polling while the backend builds it). Those graphs
  // are kept in a pool, never merged in wholesale: the user picks which
  // company to add. Each fetch may start a link run on the backend (SEC
  // filings + an LLM pass, cached for 7 days), so nothing is fetched until
  // the list is first opened, and then a couple of companies at a time. The
  // fetches ask for links only: a company's news run adds no companies.
  // `poolVersion` re-renders the candidate list as graphs arrive; the pool
  // itself is a ref because its callbacks outlive the renders that made them.
  const expansionRef = useRef({
    pool: new Map<string, CompanyGraph>(),
    inflight: new Map<string, () => void>(),
    wanted: false,
  });
  const [poolVersion, setPoolVersion] = useState(0);
  const [expandingCount, setExpandingCount] = useState(0);
  const graphDone = state.graphStatus !== "loading";
  useEffect(() => {
    const graph = state.graph;
    const exp = expansionRef.current;
    if (!graph || !graphDone || !exp.wanted) return;
    const frontier = graph.nodes
      .filter((n) => n.kind === "company")
      .filter((n) => !exp.pool.has(n.id) && !exp.inflight.has(n.id))
      .sort((a, b) => Number(!!b.isCenter) - Number(!!a.isCenter));
    for (const node of frontier) {
      if (exp.inflight.size >= EXPAND_CONCURRENCY) break;
      const finish = (addition?: CompanyGraph) => {
        exp.inflight.get(node.id)?.();
        exp.inflight.delete(node.id);
        exp.pool.set(node.id, addition ?? { nodes: [], edges: [] });
        setExpandingCount(exp.inflight.size);
        setPoolVersion((n) => n + 1); // also frees the slot: this effect runs again
      };
      const stop = pollCompanyGraph(node.id, {
        onUpdate: ({ graph: addition, status }) => {
          if (status !== "running") finish(addition);
        },
        onFailure: () => finish(), // a company whose graph can't be read just offers nothing
      }, true);
      exp.inflight.set(node.id, stop);
    }
    setExpandingCount(exp.inflight.size);
  }, [state.graph, graphDone, poolVersion]);

  const candidates = useMemo(
    () => (state.graph ? relatedCandidates(state.graph, expansionRef.current.pool) : []),
    // poolVersion is the pool's change signal; the Map itself is mutated in place.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state.graph, poolVersion],
  );
  const openPicker = () => {
    if (expansionRef.current.wanted) return;
    expansionRef.current.wanted = true;
    setPoolVersion((n) => n + 1);
  };
  const addCandidate = (candidate: Candidate) => {
    if (!state.ticker) return;
    dispatch({
      type: "GRAPH_EXPANDED",
      ticker: state.ticker,
      addition: additionFor(candidate, expansionRef.current.pool),
    });
  };

  // Measure the full-bleed container so the force graph fills it exactly.
  // containerEl is a callback-ref value (not a useRef), so this effect re-runs
  // and re-attaches whenever the underlying DOM node changes — e.g. when the
  // stage transition below swaps <EmptyCanvas>'s div out for the main div.
  useEffect(() => {
    if (!containerEl) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width, height });
    });
    observer.observe(containerEl);
    return () => observer.disconnect();
  }, [containerEl]);

  // Force a repaint when the theme/mode changes — the canvas only redraws on
  // its own render loop or a data/view change, so once physics settles and
  // that loop stops, toggling dark mode would otherwise leave node labels
  // showing whatever color was baked into the last paint instead of picking
  // up the new --text-primary.
  useEffect(() => {
    fgRef.current?.resumeAnimation();
  }, [mode, theme]);

  // Swap the default free-form physics for the supply-chain layout once the
  // ForceGraph2D instance exists (it only mounts after the first node is
  // visible). Positions come entirely from layoutRef: links still draw but
  // no longer pull, and there's no repulsion or recentering to fight the
  // layout — that's what gives every node its even spacing.
  const graphMounted = visible.nodes.length > 0 && size.width > 0;
  useEffect(() => {
    const fg = fgRef.current;
    if (!graphMounted || !fg) return;
    fg.d3Force("link")?.strength(0);
    fg.d3Force("charge", null);
    fg.d3Force("center", null);
    fg.d3Force(
      "layout",
      targetForce((id) => layoutRef.current.targets.get(id), LAYOUT_PULL),
    );
  }, [graphMounted]);

  // Stage 1 — fetch the company graph, and keep polling while the backend is
  // still building it. The cleanup only cancels a poll when the user has
  // actually moved on: a new search, an opened project, a reset.
  //
  // Keyed on the graph session, not the stage: the poll outlives the build-out
  // (the stage moves on once the links are in) to pick up the highlights from
  // the news run that follows. A session starts a poll only when it begins in
  // "building-graph", so an opened project never fetches.
  const stageRef = useRef(state.stage);
  stageRef.current = state.stage;
  useEffect(() => {
    const ticker = state.ticker;
    if (stageRef.current !== "building-graph" || !ticker) return;
    return pollCompanyGraph(ticker, {
      onUpdate: ({ graph, status, linksDone }) =>
        dispatch({ type: "GRAPH_LOADED", ticker, graph, status, linksDone }),
      onFailure: (reason) => {
        setGraphFailure(reason);
        dispatch({ type: "GRAPH_STATUS", status: "error" });
      },
    });
  }, [state.ticker, state.graphSession, dispatch]);

  // A new graph (search, opened project, reset) starts a new build-out from
  // an empty canvas. Keyed on graphSession rather than on `state.graph`
  // changing: the graph also changes every time a poll brings more links,
  // and those must be merged into what's on screen, not restart it. Declared
  // before the build-out effect so that, when both fire in one commit, the
  // canvas is cleared first.
  useEffect(() => {
    const build = buildRef.current;
    build.token = {};
    build.graph = null;
    build.queued = new Set();
    build.nextRevealAt = 0;
    build.boards = new Map();
    build.seats = [];
    build.finishing = false;
    build.peopleRevealed = false;
    fitZoomRef.current = null;
    layoutRef.current = EMPTY_LAYOUT;
    movedCompaniesRef.current.clear();
    expansionRef.current.inflight.forEach((stop) => stop());
    expansionRef.current = { pool: new Map(), inflight: new Map(), wanted: false };
    setExpandingCount(0);
    setPoolVersion(0);
    setSelectedId(null);
    setGraphFailure(null);
    setVisible({ nodes: [], edges: [] });
    return () => {
      build.timers.forEach((t) => window.clearTimeout(t));
      build.timers = [];
    };
  }, [state.graphSession]);

  // The build-out: companies appear one at a time, then every board member
  // one at a time. It runs on every graph update but only ever *adds* to
  // what's on screen — a company already revealed keeps its node object, and
  // with it its position and any pin from dragging.
  //
  // Its timers deliberately aren't cancelled by this effect's own cleanup.
  // They used to be, back when people were loaded by an effect gated on the
  // stage: TagGenerationStep moved the stage on almost immediately, the
  // cleanup cancelled the remaining staggered timers, and only the first
  // company or two ever got their people. Only a new build-out (the effect
  // above) cancels them now.
  useEffect(() => {
    const graph = state.graph;
    if (!graph) return;
    const build = buildRef.current;
    const token = build.token;
    build.graph = graph;

    const later = (fn: () => void, ms: number) => {
      build.timers.push(
        window.setTimeout(() => {
          if (build.token === token) fn();
        }, ms),
      );
    };

    // Recompute every node's slot from the companies and board seats known
    // so far. Called whenever either changes; nodes glide to their new slots.
    const relayout = () => {
      const current = build.graph;
      if (!current) return;
      const companies = current.nodes.filter((n) => n.kind === "company");
      const ids = new Set(companies.map((c) => c.id));
      layoutRef.current = computeGraphLayout(
        companies,
        current.edges,
        build.seats
          .filter((s) => ids.has(s.edge.source))
          .map((s) => ({ companyId: s.edge.source, personId: s.person.id })),
      );
      // A fresh layout knows nothing about companies the user has dragged —
      // move them (and their boards) back to where they were dropped.
      movedCompaniesRef.current.forEach((to, id) =>
        moveCompany(layoutRef.current, id, to),
      );
      fgRef.current?.d3ReheatSimulation();
    };

    const fitCamera = () => {
      fgRef.current?.zoomToFit(ZOOM_FIT_FINAL_MS, ZOOM_FIT_FINAL_PADDING);
      later(() => {
        fitZoomRef.current = fgRef.current?.zoom() ?? null;
      }, ZOOM_FIT_FINAL_MS + 50);
    };

    // Bring `candidates` onto the canvas one at a time, each popping out of
    // its company. Only seats of companies still in the graph are shown.
    const revealPeople = (candidates: Seat[]) => {
      const current = build.graph;
      if (!current) return;
      build.peopleRevealed = true;
      const ids = new Set(current.nodes.map((n) => n.id));
      const seats = candidates.filter((s) => ids.has(s.edge.source));
      if (seats.length === 0) {
        later(fitCamera, SETTLE_PAUSE_MS * 2);
        return;
      }
      seats.forEach(({ person, edge }, j) => {
        later(() => {
          setVisible((prev) => {
            const alreadyShown = prev.nodes.some((n) => n.id === person.id);
            // prev.nodes are the same objects the simulation mutates, so
            // the company's x/y here is where it currently sits on screen —
            // each person pops out from its own company, not the origin.
            const company = prev.nodes.find((n) => n.id === edge.source) as
              PositionedNode | undefined;
            const nodes = alreadyShown
              ? prev.nodes
              : [
                  ...prev.nodes,
                  {
                    ...person,
                    x: (company?.x ?? 0) + (Math.random() - 0.5) * 12,
                    y: (company?.y ?? 0) + (Math.random() - 0.5) * 12,
                  },
                ];
            return { nodes, edges: [...prev.edges, { ...edge }] };
          });
          if (j === seats.length - 1) later(fitCamera, SETTLE_PAUSE_MS * 2);
        }, j * PERSON_STAGGER_MS);
      });
    };

    const companies = graph.nodes.filter((n) => n.kind === "company");
    const companyIds = new Set(companies.map((c) => c.id));

    // Bring what's already on screen in line with this version of the graph:
    // new edges between visible companies, and nothing the graph has dropped.
    build.queued.forEach((id) => {
      if (!companyIds.has(id)) build.queued.delete(id);
    });
    setVisible((prev) => {
      const nodes = prev.nodes.some(
        (n) => n.kind === "company" && !companyIds.has(n.id),
      )
        ? prev.nodes.filter((n) => n.kind !== "company" || companyIds.has(n.id))
        : prev.nodes;
      const edges = syncEdges(nodes, prev.edges, graph);
      return nodes === prev.nodes && edges === prev.edges
        ? prev
        : { nodes, edges };
    });

    // Queue the companies that are new in this version, center first, each
    // one a stagger after the last (including ones queued by earlier versions).
    const now = performance.now();
    const fresh = companies
      .filter((c) => !build.queued.has(c.id))
      .sort((a, b) => Number(!!b.isCenter) - Number(!!a.isCenter));
    for (const node of fresh) {
      build.queued.add(node.id);
      const at = Math.max(now, build.nextRevealAt);
      build.nextRevealAt = at + REVEAL_STAGGER_MS;
      later(() => {
        setVisible((prev) => {
          if (prev.nodes.some((n) => n.id === node.id)) return prev;
          // The searched ticker is pinned at the graph's origin via d3-force's
          // `fx`/`fy` (a position the simulation treats as immovable, vs.
          // `x`/`y` which it's free to move). That's (0, 0) in *graph space*,
          // which react-force-graph's default camera already centers on the
          // canvas. Every other node spawns stacked on that same point (with
          // a small jitter so d3-force doesn't see exactly-coincident points)
          // so it visually pops OUT from the center, rather than
          // materializing at some random/scattered start point.
          const spawn = node.isCenter
            ? { fx: 0, fy: 0 }
            : { x: (Math.random() - 0.5) * 24, y: (Math.random() - 0.5) * 24 };
          const nodes = [...prev.nodes, { ...node, ...spawn }];
          // Against the latest graph, not the one that queued this node: a
          // later poll may have brought more of its edges in the meantime.
          return {
            nodes,
            edges: syncEdges(nodes, prev.edges, build.graph ?? graph),
          };
        });
      }, at - now);
    }

    // Fetch each company's board as soon as the company is known, alongside
    // the reveal: row and column spacing depend on board sizes, so learning
    // them early lets companies settle into correctly-spaced spots before
    // people appear. One entry per board seat (company -> person edge): a
    // director on two boards appears twice — the first adds the node, the
    // second just adds the extra interlock edge.
    for (const c of companies) {
      if (build.boards.has(c.id)) continue;
      build.boards.set(
        c.id,
        fetchBoardNetwork(c.id)
          .then((board) => {
            if (build.token !== token) return;
            const seats = board.edges.flatMap((edge) => {
              const person = board.nodes.find(
                (n) => n.id === edge.target && n.kind === "person",
              );
              return person ? [{ person, edge }] : [];
            });
            if (seats.length === 0) return;
            build.seats.push(...seats);
            relayout();
            // A board that lands after the first reveal (a company the hop
            // slider added) would otherwise never be drawn: that reveal runs
            // once, over the seats known at the time.
            if (build.peopleRevealed) revealPeople(seats);
          })
          .catch(() => undefined), // a board that fails to load just leaves that company without people
      );
    }

    relayout();

    // Once the graph is complete ("done", or "error" with whatever links
    // were found) and its last company has appeared, move the workspace on
    // and bring in the people.
    if (state.graphStatus !== "loading" && !build.finishing) {
      build.finishing = true;
      later(
        () => {
          dispatch({ type: "GRAPH_READY" });
          Promise.all(build.boards.values()).then(() => {
            if (build.token === token) revealPeople(build.seats);
          });
        },
        Math.max(0, build.nextRevealAt - now) + SETTLE_PAUSE_MS,
      );
    }
  }, [state.graph, state.graphStatus, dispatch]);

  if (state.stage === "empty" && !state.graph) {
    // Still render the dot-grid canvas — TickerInput overlays on top of it, and
    // the empty canvas itself is part of the "this is a workspace" signal.
    return <EmptyCanvas containerRef={setContainerEl} />;
  }

  // Looked up among what's drawn, so a node the graph dropped also closes its card.
  const selectedNode = visible.nodes.find((n) => n.id === selectedId);
  const selectedBoards =
    selectedNode?.kind === "person"
      ? visible.edges
          .filter(
            (e) =>
              e.relationship === "board-interlock" &&
              endId(e.target) === selectedNode.id,
          )
          .flatMap((e) => visible.nodes.filter((n) => n.id === endId(e.source)))
      : [];
  const hasPeople = visible.nodes.some((n) => n.kind === "person");
  const hasHighlights = visible.nodes.some((n) => highlightBySymbol.has(n.id));

  return (
    <div
      ref={setContainerEl}
      className="relative h-full w-full overflow-hidden"
      style={{ background: "var(--bg)" }}
    >
      <DotGrid />

      {state.graphStatus === "loading" && visible.nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-sm" style={{ color: "var(--text-tertiary)" }}>
            Building {state.ticker}'s network…
          </span>
        </div>
      )}

      {state.graphStatus === "error" && visible.nodes.length === 0 && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
          <span className="text-sm" style={{ color: "var(--status-negative)" }}>
            {graphFailure === "unknown-ticker"
              ? `No listed company found for "${state.ticker}". Check the ticker and try again.`
              : "Couldn't reach the graph service. Try again in a moment."}
          </span>
          {/* The ticker input only shows on the empty stage, so without this
              a failed search would be a dead end. */}
          <button
            type="button"
            onClick={() => dispatch({ type: "TICKER_CLEARED" })}
            className="px-3 py-1.5 text-xs font-medium"
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-control)",
              color: "var(--text-primary)",
            }}
          >
            Search another ticker
          </button>
        </div>
      )}

      {/* Progress and partial-failure notes while there's already a graph on
          screen — the backend returns the links it has before it's finished. */}
      {visible.nodes.length > 0 &&
        (state.graphStatus !== "done" ||
          state.graphNewsRunning ||
          state.graph?.nodes.length === 1) && (
          <div
            className="absolute left-1/2 top-6 z-10 -translate-x-1/2 px-3 py-1.5 text-xs"
            role="status"
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-control)",
              color:
                state.graphStatus === "error"
                  ? "var(--status-negative)"
                  : "var(--text-secondary)",
            }}
          >
            {state.graphStatus === "error"
              ? `Some of ${state.ticker}'s relationships couldn't be loaded — showing what was found.`
              : state.graphStatus === "done"
                ? state.graph?.nodes.length === 1
                  ? `No relationships found for ${state.ticker}.`
                  : "Checking recent news for these companies…"
                : `Reading filings for ${state.ticker}…`}
          </div>
        )}

      {visible.nodes.length > 0 && size.width > 0 && (
        <ForceGraph2D
          ref={fgRef}
          graphData={graphData}
          nodeId="id"
          width={size.width}
          height={size.height}
          backgroundColor="rgba(0,0,0,0)"
          cooldownTicks={200}
          d3VelocityDecay={0.3}
          // The library stops redrawing once the simulation cools; a flash is
          // time-based, so keep frames coming only while something is flashing.
          autoPauseRedraw={!hasFlashes}
          // Click a node for its details; click it again, or the empty
          // canvas, to dismiss. A drag doesn't count as a click.
          onNodeClick={(node: NodeObject<GraphNode>) => {
            const id = String(node.id);
            setSelectedId((prev) => (prev === id ? null : id));
          }}
          onBackgroundClick={() => setSelectedId(null)}
          // A company's board follows it live while it's being dragged.
          onNodeDrag={(node: NodeObject<GraphNode>) => {
            if (node.kind !== "company") return;
            moveCompany(layoutRef.current, String(node.id), {
              x: node.x ?? 0,
              y: node.y ?? 0,
            });
          }}
          // react-force-graph releases a node when the drag ends, and the
          // layout force would then pull it straight back to its slot.
          // Re-pinning it with fx/fy where it was dropped makes the move stick.
          onNodeDragEnd={(node: NodeObject<GraphNode>) => {
            node.fx = node.x;
            node.fy = node.y;
            if (node.kind === "company") {
              movedCompaniesRef.current.set(String(node.id), {
                x: node.x ?? 0,
                y: node.y ?? 0,
              });
            }
          }}
          linkColor={(link) =>
            RELATIONSHIP_COLORS[(link as unknown as GraphEdge).relationship]
          }
          // Board-interlock edges (company -> board member) read as a
          // secondary, quieter relationship than the primary company-to-
          // company ones, so they stay visibly thinner.
          linkWidth={(link) =>
            (link as unknown as GraphEdge).relationship === "board-interlock"
              ? 1
              : 3
          }
          // Color alone says an edge is a supply relationship but not which
          // way it runs, so those edges get an arrowhead at their midpoint
          // pointing the way goods flow: supplier -> customer. Drawn by hand
          // because the built-in arrows always point source -> target, and a
          // "supplier" edge runs the other way (its target supplies its source).
          linkCanvasObjectMode={() => "after"}
          linkCanvasObject={(link, ctx) => {
            const { relationship } = link as unknown as GraphEdge;
            if (relationship !== "supplier" && relationship !== "customer")
              return;
            // GraphEdge types the ends as id strings, but the simulation swaps
            // them for the live node objects once the link is in play.
            const { source, target } = link as unknown as {
              source: PositionedNode | string;
              target: PositionedNode | string;
            };
            if (typeof source !== "object" || typeof target !== "object")
              return;
            const [from, to] =
              relationship === "supplier" ? [target, source] : [source, target];
            const dx = (to.x ?? 0) - (from.x ?? 0);
            const dy = (to.y ?? 0) - (from.y ?? 0);
            const len = Math.hypot(dx, dy);
            if (len < ARROW_LENGTH * 3) return;
            const ux = dx / len;
            const uy = dy / len;
            const tipX = (from.x ?? 0) + dx / 2 + (ux * ARROW_LENGTH) / 2;
            const tipY = (from.y ?? 0) + dy / 2 + (uy * ARROW_LENGTH) / 2;
            const baseX = tipX - ux * ARROW_LENGTH;
            const baseY = tipY - uy * ARROW_LENGTH;
            ctx.beginPath();
            ctx.moveTo(tipX, tipY);
            ctx.lineTo(
              baseX - uy * ARROW_HALF_WIDTH,
              baseY + ux * ARROW_HALF_WIDTH,
            );
            ctx.lineTo(
              baseX + uy * ARROW_HALF_WIDTH,
              baseY - ux * ARROW_HALF_WIDTH,
            );
            ctx.closePath();
            ctx.fillStyle = RELATIONSHIP_COLORS[relationship];
            ctx.fill();
          }}
          nodeCanvasObject={(node: NodeObject<GraphNode>, ctx, globalScale) => {
            const x = node.x ?? 0;
            const y = node.y ?? 0;
            const isCenter = !!node.isCenter;
            const isPerson = node.kind === "person";
            const r = nodeRadius(node);
            const highlight =
              isCenter || isPerson ? undefined : highlightBySymbol.get(node.id);
            const flash = isPerson
              ? undefined
              : flashRef.current.get(String(node.id));

            if (flash) {
              // Live move: a neutral breathing ring (~1.2s). No color on purpose: green and red on a
              // node are reserved for news highlights, and a market's Yes direction is not a stock call.
              const phase =
                (Math.sin((performance.now() / 1200) * 2 * Math.PI) + 1) / 2; // 0..1
              const ring = cssVar("--text-primary", "#111");
              ctx.beginPath();
              ctx.arc(x, y, r + 4 + phase * 8, 0, 2 * Math.PI);
              ctx.fillStyle = ring;
              ctx.globalAlpha = 0.08 + phase * 0.18;
              ctx.fill();
              ctx.globalAlpha = 1;
              ctx.beginPath();
              ctx.arc(x, y, r + 2, 0, 2 * Math.PI);
              ctx.lineWidth = 1.5;
              ctx.strokeStyle = ring;
              ctx.globalAlpha = 0.5 + phase * 0.5;
              ctx.stroke();
              ctx.globalAlpha = 1;
            }

            if (highlight) {
              // A recent news event is a steady halo in its direction's status color. Motion is
              // reserved for prediction markets: a live move on the same node adds its pulsing
              // neutral ring on top, so color always reads as news and a pulse as a market.
              const color = cssVar(
                DIRECTION_TOKEN[highlight.direction],
                highlight.direction === "may_benefit" ? "#16a34a" : "#dc2626",
              );
              ctx.beginPath();
              ctx.arc(x, y, r + 6, 0, 2 * Math.PI);
              ctx.fillStyle = color;
              ctx.globalAlpha = 0.22;
              ctx.fill();
              ctx.globalAlpha = 1;
            }
            ctx.beginPath();
            ctx.arc(x, y, r, 0, 2 * Math.PI);
            ctx.fillStyle = isCenter
              ? cssVar("--accent", "#3b5bdb")
              : isPerson
                ? RELATIONSHIP_COLORS["board-interlock"]
                : highlight
                  ? cssVar(DIRECTION_TOKEN[highlight.direction], "#16a34a")
                  : cssVar("--surface-elevated", "#fff");
            ctx.fill();
            if (!isCenter && !isPerson && !highlight) {
              ctx.lineWidth = 1.5;
              ctx.strokeStyle = cssVar("--border", "#ddd");
              ctx.stroke();
            }
            // Ring around the node whose details card is open.
            if (node.id === selectedId) {
              ctx.beginPath();
              ctx.arc(x, y, r + 2.5, 0, 2 * Math.PI);
              ctx.lineWidth = 1.5;
              ctx.strokeStyle = cssVar("--accent", "#3b5bdb");
              ctx.stroke();
            }

            // Person nodes stay unlabeled dots until the user zooms in far
            // enough — the main graph would get cluttered with a name for
            // every board member the instant one company's board loads.
            const fitZoom = fitZoomRef.current;
            if (
              isPerson &&
              (fitZoom === null ||
                globalScale < fitZoom * PERSON_LABEL_ZOOM_MULTIPLIER)
            )
              return;

            if (isPerson) {
              // Initials only (the full name is in the node card): lighter,
              // smaller and secondary-colored than company names, and always
              // horizontal. Each goes on the side of its dot that faces away
              // from the company, so a ring's labels spread outward.
              const anchor = layoutRef.current.personAnchors.get(
                String(node.id),
              ) ?? { x: 0, y: 0 };
              const onLeft = x < anchor.x;
              ctx.font = `300 ${Math.max(9 / globalScale, 2.5)}px "Plus Jakarta Sans", sans-serif`;
              ctx.textAlign = onLeft ? "right" : "left";
              ctx.textBaseline = "middle";
              ctx.fillStyle = cssVar("--text-secondary", "#555");
              ctx.fillText(initials(node.label), onLeft ? x - r - 2 : x + r + 2, y);
              return;
            }

            const fontSize = Math.max(14 / globalScale, 5);
            ctx.font = `${isCenter ? "600" : "500"} ${fontSize}px "Plus Jakarta Sans", sans-serif`;
            ctx.textAlign = "center";
            // The layout keeps each board on one side of its company and
            // tells us which side is left free for the name.
            const above = layoutRef.current.labelAbove.has(String(node.id));
            ctx.textBaseline = above ? "bottom" : "top";
            const labelY = above ? y - r - 2 : y + r + 2;
            // Edges still run under the name, so a background patch keeps it
            // legible.
            const textWidth = ctx.measureText(node.label).width;
            const padX = fontSize * 0.35;
            const padY = fontSize * 0.15;
            ctx.fillStyle = cssVar("--bg", "#fff");
            ctx.globalAlpha = 0.85;
            ctx.fillRect(
              x - textWidth / 2 - padX,
              (above ? labelY - fontSize : labelY) - padY,
              textWidth + padX * 2,
              fontSize + padY * 2,
            );
            ctx.globalAlpha = 1;
            ctx.fillStyle = cssVar("--text-primary", "#111");
            ctx.fillText(node.label, x, labelY);
          }}
          nodePointerAreaPaint={(node: NodeObject<GraphNode>, color, ctx) => {
            const x = node.x ?? 0;
            const y = node.y ?? 0;
            const r = nodeRadius(node) + 3;
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(x, y, r, 0, 2 * Math.PI);
            ctx.fill();
          }}
        />
      )}

      {selectedNode && state.graph && (
        <NodeInfoCard
          node={selectedNode}
          graph={state.graph}
          boards={selectedBoards}
          flash={flashBySymbol.get(String(selectedNode.id))}
          onClose={() => setSelectedId(null)}
        />
      )}
      {/* Bottom-left so it clears the node card at the top-left. */}
      <div className="absolute bottom-6 left-6 z-10">
        <GraphLegend
          showBoardInterlock={hasPeople}
          showHighlights={hasHighlights}
          ticker={state.ticker}
        />
      </div>
      {/* Bottom-right, beside the market sidebar's live events. */}
      {state.graph && (
        <AddCompanyPicker
          candidates={candidates}
          reading={expandingCount}
          onOpen={openPicker}
          onAdd={addCandidate}
        />
      )}
    </div>
  );
}

function DotGrid() {
  return (
    <div
      className="pointer-events-none absolute inset-0"
      style={{
        backgroundImage:
          "radial-gradient(circle, var(--grid-dot) 1px, transparent 1px)",
        backgroundSize: "28px 28px",
        // Tiles from (0,0) by default, so unless the container's size is an
        // exact multiple of 28px the pattern reads as lopsided — flush dots
        // on the top/left edge, a clipped partial dot on the bottom/right.
        // Centering the pattern itself makes it symmetric around the same
        // midpoint the ticker input and the pinned center node use.
        backgroundPosition: "center center",
      }}
    />
  );
}

function EmptyCanvas({
  containerRef,
}: {
  containerRef: (el: HTMLDivElement | null) => void;
}) {
  return (
    <div
      ref={containerRef}
      className="relative h-full w-full overflow-hidden"
      style={{ background: "var(--bg)" }}
    >
      <DotGrid />
    </div>
  );
}
