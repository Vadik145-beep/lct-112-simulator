import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { getAccessToken, setAccessToken, setSessionMarker } from "@/api/token";

export type ApiError = { error: { code: string; message: string } };
export type UserOut = components["schemas"]["UserOut"];
export type TokenResponse = components["schemas"]["TokenResponse"];
export type Role = UserOut["role"];

const AUTH_PATHS = ["/api/auth/login", "/api/auth/refresh", "/api/auth/logout", "/api/auth/demo/"];

let refreshing: Promise<boolean> | null = null;

/** Asks the backend for a new access token using the refresh cookie. */
export async function refreshAccessToken(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch("/api/auth/refresh", { method: "POST", credentials: "include" });
        if (!res.ok) {
          setAccessToken(null);
          setSessionMarker(false);
          return false;
        }
        const data = (await res.json()) as TokenResponse;
        setAccessToken(data.access_token);
        return true;
      } catch {
        return false;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = getAccessToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  async onResponse({ request, response }) {
    const isAuthCall = AUTH_PATHS.some((p) => new URL(request.url).pathname.startsWith(p));
    if (response.status !== 401 || isAuthCall || request.headers.get("x-retried")) return response;
    if (!(await refreshAccessToken())) return response;
    const retry = new Request(request, { headers: new Headers(request.headers) });
    retry.headers.set("Authorization", `Bearer ${getAccessToken()}`);
    retry.headers.set("x-retried", "1");
    return fetch(retry);
  },
};

// Same-origin API. The origin is spelled out because Request() rejects relative URLs
// outside the browser (tests); fetch is resolved at call time so tests can stub it.
export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "http://localhost",
  credentials: "include",
  fetch: (input) => globalThis.fetch(input),
});
api.use(authMiddleware);

/** Extracts the Russian message from an API error body or gives a generic fallback. */
export function errorMessage(error: unknown, fallback = "Не удалось выполнить запрос. Попробуйте ещё раз."): string {
  if (error && typeof error === "object" && "error" in error) {
    const inner = (error as { error?: { message?: string } }).error;
    if (inner?.message) return inner.message;
  }
  return fallback;
}
