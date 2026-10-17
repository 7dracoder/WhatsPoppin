import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { WorkspaceProvider } from "../features/workspace/WorkspaceProvider";
import { TickerInput, GraphCanvas } from "../features/workspace/graph";
import { PredictionMarketPanel } from "../features/workspace/graph/PredictionMarketPanel";
import { useWorkspace } from "../features/workspace/useWorkspace";
import { ResizableAside } from "../features/workspace/shared/ResizableAside";
import {
  WorkspaceTransition,
  OverlayChart,
  EvidenceSidebar,
  EvidenceDrawer,
  QueryCard,
} from "../features/workspace/market-view";
import {
  ProjectSidebar,
  SaveProjectButton,
} from "../features/workspace/projects";
import { AlertsFeed, LiveAlertsProvider } from "../features/workspace/alerts";

/**
 * Left to right: the projects rail's resting width (`left-14`), the graph,
 * then the prediction-market sidebar. The graph's pane is the space *between*
 * the rail and the sidebar, so the pinned center node and ticker input center
 * on that space and nothing is drawn under either. The sidebar only exists once
 * a search has started; before that the ticker prompt centers on the full
 * width. (The rail still overlays the graph when it expands on hover; that's a
 * temporary preview.)
 */
function GraphColumn() {
  const { state } = useWorkspace();
  return (
    <div className="relative h-full w-full">
      <div className="absolute inset-y-0 left-14 right-0 flex">
        <div className="relative min-w-0 flex-1">
          <TickerInput />
          <GraphCanvas />
        </div>
        {state.stage !== "empty" && (
          <ResizableAside
            storageKey="workspace.marketsSidebarWidth"
            widthVar="--markets-sidebar-width"
          >
            <PredictionMarketPanel ticker={state.ticker} />
          </ResizableAside>
        )}
      </div>
    </div>
  );
}

function MarketViewColumn() {
  return (
    <div className="flex h-full w-full">
      {/* pl-20 (not p-6 on all sides): the projects rail overlays this pane at
          a higher z-index, so content needs clearance from the true left edge.
          The chart takes whatever height is left over, so the query card ends up
          flush with the bottom of the screen on a tall window; min-h keeps the
          chart usable on a short one, and the column scrolls when everything
          together is taller than the window. Save is a small square button at
          the bottom-right of the query card. */}
      <div className="relative min-w-0 flex-1">
        <div className="flex h-full flex-col gap-5 overflow-y-auto pb-6 pl-20 pr-6 pt-6">
          <OverlayChart className="flex min-h-[780px] flex-1 flex-col gap-3 rounded-[var(--radius-panel)] p-4" />
          <div className="shrink-0">
            <QueryCard action={<SaveProjectButton />} />
          </div>
        </div>
      </div>
      <ResizableAside
        storageKey="workspace.evidenceSidebarWidth"
        widthVar="--evidence-sidebar-width"
      >
        <EvidenceSidebar />
      </ResizableAside>
    </div>
  );
}

export function WorkspaceShell() {
  // ProjectSidebar positions itself as an absolute overlay (see its own
  // className) rather than a flex sibling that reserves width — if it
  // consumed layout space, its collapsed/hover/pinned width changes would
  // shift this pane's horizontal center out from under the ticker input
  // and the pinned center node every time the rail opens or closes.
  //
  // The "What'sPoppin" link in the header points at this page. Clicking it while
  // already here changes nothing in the URL, but the router still records a
  // navigation with a new `location.key`; that is the signal to start over at
  // the ticker prompt. The first key (page load, arriving from a project page)
  // is skipped so a fresh mount isn't reset twice.
  const { state, dispatch } = useWorkspace();
  const { key } = useLocation();
  const firstKey = useRef(key);
  useEffect(() => {
    if (key !== firstKey.current) dispatch({ type: "RESET" });
  }, [key, dispatch]);

  return (
    <div className="relative h-full w-full">
      <WorkspaceTransition
        className="relative h-full w-full overflow-hidden"
        graphView={<GraphColumn />}
        marketView={<MarketViewColumn />}
      />
      <ProjectSidebar />
      <EvidenceDrawer />
      {/* The live-alerts bell belongs to the graph stages: in the market view the
          chart and evidence sidebar are the focus, and the bell's panel would
          cover the evidence picker. */}
      {state.stage !== "market-view" && <AlertsFeed />}
    </div>
  );
}

export default function WorkspacePage() {
  return (
    <WorkspaceProvider>
      <LiveAlertsProvider>
        <WorkspaceShell />
      </LiveAlertsProvider>
    </WorkspaceProvider>
  );
}
