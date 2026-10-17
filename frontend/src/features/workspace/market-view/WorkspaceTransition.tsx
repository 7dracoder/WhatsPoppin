import { motion } from "motion/react";
import type { ReactNode } from "react";
import { useWorkspace } from "../useWorkspace";

export interface WorkspaceTransitionProps {
  /** Stage 0-4 content: ticker input, graph canvas, tag chips, market card picker. */
  graphView: ReactNode;
  /** Stage 6+ content: the overlay chart and anything stacked above/below it. */
  marketView: ReactNode;
  className?: string;
}

/**
 * Stage 5 — hosts both the graph-building views and the market-view content
 * side by side in a double-width track, then slides the track horizontally
 * when `state.stage` flips to "market-view" (dispatched elsewhere by the
 * "Generate" button). This is a state-driven slide, not a route change —
 * per the design spec, there's no reason to deep-link into an intermediate,
 * unsaved step, so both halves stay mounted in one view and we animate
 * `x` instead of swapping pages.
 */
export function WorkspaceTransition({ graphView, marketView, className }: WorkspaceTransitionProps) {
  const { state } = useWorkspace();
  const isMarketView = state.stage === "market-view";

  return (
    <div className={className ?? "relative h-full min-h-screen w-full overflow-hidden"}>
      <motion.div
        className="flex h-full w-[200%]"
        animate={{ x: isMarketView ? "-50%" : "0%" }}
        initial={false}
        transition={{ type: "spring", stiffness: 240, damping: 30, mass: 1 }}
      >
        <div
          className="h-full w-1/2 shrink-0"
          aria-hidden={isMarketView}
          style={isMarketView ? { pointerEvents: "none" } : undefined}
        >
          {graphView}
        </div>
        <div
          className="h-full w-1/2 shrink-0"
          aria-hidden={!isMarketView}
          style={!isMarketView ? { pointerEvents: "none" } : undefined}
        >
          {marketView}
        </div>
      </motion.div>
    </div>
  );
}
