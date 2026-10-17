import { createContext, useReducer, type Dispatch, type ReactNode } from "react";
import { initialWorkspaceState, type WorkspaceState } from "../../types/project";
import { workspaceReducer, type WorkspaceAction } from "./workspaceReducer";

export const WorkspaceContext = createContext<
  { state: WorkspaceState; dispatch: Dispatch<WorkspaceAction> } | null
>(null);

/**
 * Holds the whole Stage 0-9 flow as one reducer (see frontend-architecture-spec.md
 * Section 1 for why useReducer over Zustand/Redux here: one state machine, a
 * handful of fields, no distant unrelated consumers yet).
 */
export function WorkspaceProvider({
  children,
  initialState = initialWorkspaceState,
}: {
  children: ReactNode;
  initialState?: WorkspaceState;
}) {
  const [state, dispatch] = useReducer(workspaceReducer, initialState);
  return <WorkspaceContext.Provider value={{ state, dispatch }}>{children}</WorkspaceContext.Provider>;
}
