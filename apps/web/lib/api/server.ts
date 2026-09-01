import { cookies } from "next/headers";

import { ApiError } from "./errors";
import type { ApiErrorBody, Project, Session } from "./types";

const INTERNAL_API_BASE_URL =
  process.env.INTERNAL_API_BASE_URL ?? "http://127.0.0.1:8000";

/**
 * Server-side read of the API, forwarding the browser's session cookie.
 * Server components never hold credentials of their own.
 */
async function serverFetch<T>(path: string): Promise<T> {
  const cookieStore = await cookies();
  const cookieHeader = cookieStore
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");

  const response = await fetch(`${INTERNAL_API_BASE_URL}/api${path}`, {
    headers: cookieHeader ? { Cookie: cookieHeader } : {},
    // Session-scoped data must never be served from a shared cache.
    cache: "no-store",
  });

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new ApiError(response.status, payload as ApiErrorBody | null);
  }
  return payload as T;
}

/** Returns the session, or null when the visitor is not signed in. */
export async function getSession(): Promise<Session | null> {
  try {
    return await serverFetch<Session>("/auth/me");
  } catch (error) {
    if (error instanceof ApiError && (error.status === 403 || error.status === 401)) {
      return null;
    }
    throw error;
  }
}

export async function getProjects(): Promise<Project[]> {
  return serverFetch<Project[]>("/projects");
}

export async function getProject(id: string): Promise<Project | null> {
  try {
    return await serverFetch<Project>(`/projects/${id}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}
