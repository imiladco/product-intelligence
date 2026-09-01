"use client";

import { ApiError } from "./errors";
import type { ApiErrorBody } from "./types";

const CSRF_COOKIE = "pi_csrftoken";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function readCookie(name: string): string | undefined {
  return document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`))
    ?.split("=")[1];
}

/**
 * Django sets the CSRF cookie on a GET to /api/auth/csrf. Server-side fetches
 * cannot set it for the browser, so the first unsafe request from the client
 * fetches it here.
 */
async function ensureCsrfToken(): Promise<string> {
  const existing = readCookie(CSRF_COOKIE);
  if (existing) return existing;
  await fetch("/api/auth/csrf", { credentials: "include" });
  return readCookie(CSRF_COOKIE) ?? "";
}

interface RequestOptions {
  method?: string;
  body?: unknown;
}

/**
 * The one place the browser talks to the API. Same-origin, cookie-based: no
 * token is ever stored in JavaScript.
 */
export async function apiFetch<T>(
  path: string,
  { method = "GET", body }: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (UNSAFE_METHODS.has(method)) headers["X-CSRFToken"] = await ensureCsrfToken();

  const response = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "include",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new ApiError(response.status, payload as ApiErrorBody | null);
  }
  return payload as T;
}
