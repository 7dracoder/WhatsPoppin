/**
 * Minimal typed fetch wrapper around the FastAPI backend (backend/api/).
 * Defaults to the uvicorn dev port so a fresh checkout works without a frontend/.env.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ApiError(response.status, `${method} ${path} failed: ${response.statusText}`);
  }

  // 204 No Content (a DELETE) has no body to parse.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const get = <T>(path: string) => request<T>("GET", path);
export const post = <T>(path: string, body: unknown) => request<T>("POST", path, body);
export const put = <T>(path: string, body: unknown) => request<T>("PUT", path, body);
/** `delete` is a reserved word, hence the name. */
export const del = (path: string) => request<void>("DELETE", path);
