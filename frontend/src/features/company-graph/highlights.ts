import type { GraphHighlight } from "../../types/graph";

/** Highlights for one company, newest event first. */
export function highlightsFor(highlights: GraphHighlight[], symbol: string): GraphHighlight[] {
  return highlights
    .filter((h) => h.target === symbol)
    .sort((a, b) => Date.parse(b.event_time) - Date.parse(a.event_time));
}

/** The newest highlight per company; its direction sets the node color. */
export function latestHighlightBySymbol(highlights: GraphHighlight[]): Map<string, GraphHighlight> {
  const out = new Map<string, GraphHighlight>();
  for (const h of highlights) {
    const prev = out.get(h.target);
    if (!prev || Date.parse(h.event_time) > Date.parse(prev.event_time)) out.set(h.target, h);
  }
  return out;
}
