import { ApiError, del, get, post, put } from "../apiClient";
import type { Project, ProjectInput } from "../../types/project";

/** GET /projects — most recently updated first. */
export function listProjects(): Promise<Project[]> {
  return get<Project[]>("/projects");
}

/** GET /projects/:id — resolves null on 404 so callers can render a "not found" state. */
export async function getProject(id: string): Promise<Project | null> {
  try {
    return await get<Project>(`/projects/${encodeURIComponent(id)}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

/** POST /projects — the backend assigns the id and timestamps. */
export function createProject(input: ProjectInput): Promise<Project> {
  return post<Project>("/projects", input);
}

/** DELETE /projects/:id — permanent; resolves on 204. */
export function deleteProject(id: string): Promise<void> {
  return del(`/projects/${encodeURIComponent(id)}`);
}

/** PUT /projects/:id — full replace of the editable fields. */
export function updateProject(id: string, input: ProjectInput): Promise<Project> {
  return put<Project>(`/projects/${encodeURIComponent(id)}`, input);
}
