// The access token lives only in memory; the refresh token is an httpOnly cookie.
let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

// The refresh cookie is httpOnly, so the page cannot see it. This marker remembers that a
// session was started here, so the app only asks /auth/refresh when there is something to
// refresh (avoids a 401 in the console on a fresh visit).
const SESSION_MARKER = "has_session";

export function hasSessionMarker(): boolean {
  try {
    return localStorage.getItem(SESSION_MARKER) === "1";
  } catch {
    return false;
  }
}

export function setSessionMarker(value: boolean): void {
  try {
    if (value) localStorage.setItem(SESSION_MARKER, "1");
    else localStorage.removeItem(SESSION_MARKER);
  } catch {
    // Storage unavailable: the app simply re-checks the session on the next load.
  }
}
