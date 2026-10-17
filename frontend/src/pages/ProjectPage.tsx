import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getProject } from "../lib/api/projects";
import type { Project } from "../types/project";
import { initialWorkspaceState } from "../types/project";
import { workspaceReducer } from "../features/workspace/workspaceReducer";
import { WorkspaceProvider } from "../features/workspace/WorkspaceProvider";
import { LiveAlertsProvider } from "../features/workspace/alerts";
import { WorkspaceShell } from "./WorkspacePage";

/**
 * Loads a saved Project and restores it directly into a populated Market
 * View — the graph snapshot is frozen at save time, so this never re-runs
 * the Stage 1 build animation or re-fetches a (possibly changed) live graph.
 * WorkspacePage's own WorkspaceProvider is skipped here in favor of this
 * page computing a pre-populated initial state up front.
 */
export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [status, setStatus] = useState<
    "loading" | "not-found" | "error" | "done"
  >("loading");
  const [project, setProject] = useState<Project | null>(null);

  useEffect(() => {
    if (!projectId) {
      setStatus("not-found");
      return;
    }
    let cancelled = false;
    setStatus("loading");
    getProject(projectId)
      .then((result) => {
        if (cancelled) return;
        if (!result) {
          setStatus("not-found");
          return;
        }
        setProject(result);
        setStatus("done");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  if (status === "loading") {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <span className="text-sm" style={{ color: "var(--text-tertiary)" }}>
          Loading project…
        </span>
      </div>
    );
  }

  if (status === "not-found" || status === "error" || !project) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <span className="text-sm" style={{ color: "var(--status-negative)" }}>
          {status === "not-found"
            ? "Project not found."
            : "Couldn't load this project."}
        </span>
      </div>
    );
  }

  const initialState = workspaceReducer(initialWorkspaceState, {
    type: "PROJECT_LOADED",
    project,
  });

  return (
    <WorkspaceProvider initialState={initialState}>
      <LiveAlertsProvider>
        <WorkspaceShell />
      </LiveAlertsProvider>
    </WorkspaceProvider>
  );
}
