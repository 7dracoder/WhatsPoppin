import { useEffect, useRef } from "react";
import type { AssistantAction } from "../../lib/api/assistant";
import { searchCompanies } from "../company-graph/api";
import type { ChartRange } from "../../types/market";
import type { WorkspaceState } from "../../types/project";
import type { WorkspaceAction } from "../workspace/workspaceReducer";
import { useOptionalAssistant } from "./useAssistant";

const RANGES: ChartRange[] = ["1d", "1w", "1m", "6m", "1y", "all"];
const RELATION_WORDS: Record<string, string> = {
  supplier: "supplier",
  customer: "customer",
  partner: "partner",
  competitor: "competitor",
  sector_peer: "industry peer",
  "board-interlock": "board interlock",
};

const LEGAL_SUFFIX = /[,.]?\s+(inc|corp|corporation|co|ltd|plc|llc|holdings|group|company|limited|n\.?v|s\.?a|ag|se)\.?$/i;

/** "NVIDIA CORP" -> "Nvidia", "COHERENT CORP." -> "Coherent": how a person would say it. */
export function spokenName(name: string): string {
  let n = name.trim();
  for (let i = 0; i < 3 && LEGAL_SUFFIX.test(n); i += 1) n = n.replace(LEGAL_SUFFIX, "").trim();
  if (n === n.toUpperCase() && n.length > 4) {
    n = n.toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase());
  }
  return n.replace(/,$/, "");
}

/** What the assistant is told about the workspace: everything a user could read off the screen. */
export function workspaceContext(state: WorkspaceState): Record<string, unknown> {
  const graph = state.graph;
  const center = graph?.nodes.find((n) => n.isCenter);
  const nodeName = new Map(graph?.nodes.map((n) => [n.id, n.name ?? n.label]) ?? []);
  const selected = state.suggestedMarkets.filter((m) => state.selectedMarketIds.includes(m.marketId));
  return {
    stage: state.stage,
    company: state.ticker ? { ticker: state.ticker, name: center?.name ?? null, industry: center?.industry ?? null } : null,
    graph_status: state.graphStatus,
    linked_companies: graph
      ? graph.edges
          .filter((e) => e.relationship !== "board-interlock")
          .slice(0, 30)
          .map((e) => {
            const other = e.source === state.ticker ? e.target : e.source;
            return {
              ticker: other,
              name: nodeName.get(other) ?? other,
              relationship: RELATION_WORDS[e.relationship] ?? e.relationship,
              summary: e.summary?.slice(0, 160),
            };
          })
      : [],
    board_members: graph?.nodes.filter((n) => n.kind === "person").slice(0, 15).map((n) => `${n.label}${n.role ? ` (${n.role})` : ""}`) ?? [],
    recent_events: (graph?.highlights ?? []).slice(0, 10).map((h) => ({
      company: h.target,
      effect: h.direction,
      event: h.event_type,
      reason: h.reason.slice(0, 200),
      price_change_pct: h.price_change_pct,
    })),
    tags: state.tags,
    selected_markets: selected.map((m) => ({ title: m.title, outcome: m.outcome ?? null, closes: m.closeTime })),
    chart_range: state.range,
    chart_day: state.chartDay ?? "now",
    evidence: state.evidence.slice(0, 20).map((a) => ({
      source: a.item.source,
      title: a.item.title.slice(0, 160),
      author: a.item.author,
      published: a.timestamp,
      sentiment: a.sentiment ?? null,
    })),
    query_results: state.queryResults.map((q) => q.label),
    project: state.activeProjectName,
  };
}

/**
 * Lends the workspace to the voice assistant while it's mounted: its state as context, the
 * workspace actions (search a company, open the chart, change range, go back), and narration of
 * the moments worth saying out loud (the graph finishing, the chart opening).
 */
export function useWorkspaceAssistant(state: WorkspaceState, dispatch: (action: WorkspaceAction) => void) {
  const assistant = useOptionalAssistant();
  const stateRef = useRef(state);
  stateRef.current = state;
  const register = assistant?.registerBridge;
  const announce = assistant?.announce;

  useEffect(() => {
    if (!register) return;
    register({
      getContext: () => workspaceContext(stateRef.current),
      run: async (action: AssistantAction) => {
        const s = stateRef.current;
        const value = action.value ?? "";
        switch (action.type) {
          case "search_company": {
            if (!value) return "Which company?";
            const typed = value.replace(/^\$/, "").trim();
            const found = await searchCompanies(typed).catch(() => []);
            const upper = typed.toUpperCase();
            const pick = found.find((c) => c.symbol === upper) ?? found[0];
            const ticker = pick?.symbol ?? upper;
            dispatch({ type: "TICKER_SUBMITTED", ticker });
            return;
          }
          case "generate_chart":
            if (s.selectedMarketIds.length === 0) return "Pick at least one market first.";
            if (s.stage !== "market-view") dispatch({ type: "GENERATE_PRESSED" });
            return;
          case "back_to_graph":
            if (s.stage === "market-view") dispatch({ type: "BACK_TO_GRAPH" });
            return;
          case "set_range": {
            const range = value.toLowerCase().replace(/\s+/g, "") as ChartRange;
            if (!RANGES.includes(range)) return `I can show ${RANGES.join(", ")}.`;
            dispatch({ type: "RANGE_CHANGED", range });
            return;
          }
          default:
            return;
        }
      },
    });
    return () => register(null);
  }, [register, dispatch]);

  // Narration: say what just finished, in one line, from what's on screen (no model call).
  const lastStage = useRef(state.stage);
  useEffect(() => {
    const previous = lastStage.current;
    lastStage.current = state.stage;
    if (!announce || previous === state.stage) return;
    if (state.stage === "graph-ready" && state.graph && state.ticker) {
      const center = state.graph.nodes.find((n) => n.isCenter);
      const others = state.graph.nodes.filter((n) => !n.isCenter && n.kind === "company");
      const names = others.slice(0, 3).map((n) => spokenName(n.name ?? n.label));
      const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0];
      announce(
        others.length
          ? `${spokenName(center?.name ?? state.ticker)}'s network is ready: ${others.length} linked companies, including ${list}. Pick a market on the right, or ask me to find one.`
          : `I couldn't find linked companies for ${state.ticker}.`,
      );
    } else if (state.stage === "market-view") {
      const titles = state.suggestedMarkets.filter((m) => state.selectedMarketIds.includes(m.marketId)).map((m) => m.title);
      announce(`Here's ${state.ticker} against ${titles[0] ?? "the market's odds"}${titles.length > 1 ? ` and ${titles.length - 1} more` : ""}. Ask me what the chart shows.`);
    }
  }, [state.stage, state.graph, state.ticker, state.suggestedMarkets, state.selectedMarketIds, announce]);
}
