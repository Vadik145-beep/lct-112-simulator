import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { api, errorMessage, refreshAccessToken, type Role, type TokenResponse, type UserOut } from "@/api/client";
import { hasSessionMarker, setAccessToken, setSessionMarker } from "@/api/token";
import { AuthContext, type Status } from "@/app/auth-context";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<UserOut | null>(null);
  // Set once the user logs in explicitly; a slower session restore must not override it.
  const loggedIn = useRef(false);

  const accept = useCallback((data: TokenResponse) => {
    loggedIn.current = true;
    setAccessToken(data.access_token);
    setSessionMarker(true);
    setUser(data.user);
    setStatus("authenticated");
  }, []);

  // On first load try to restore the session from the refresh cookie (only if one was set here).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = hasSessionMarker() && (await refreshAccessToken());
      if (cancelled || loggedIn.current) return;
      if (!ok) {
        setStatus("anonymous");
        return;
      }
      const { data } = await api.GET("/api/me");
      if (cancelled || loggedIn.current) return;
      if (data) {
        setUser(data);
        setStatus("authenticated");
      } else {
        setAccessToken(null);
        setStatus("anonymous");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(
    async (loginValue: string, password: string) => {
      const { data, error } = await api.POST("/api/auth/login", {
        body: { login: loginValue, password },
      });
      if (!data) throw new Error(errorMessage(error));
      accept(data);
    },
    [accept],
  );

  const demoLogin = useCallback(
    async (role: Role) => {
      const { data, error } = await api.POST("/api/auth/demo/{role}", { params: { path: { role } } });
      if (!data) throw new Error(errorMessage(error));
      accept(data);
    },
    [accept],
  );

  const logout = useCallback(async () => {
    await api.POST("/api/auth/logout");
    setAccessToken(null);
    setSessionMarker(false);
    setUser(null);
    setStatus("anonymous");
  }, []);

  const changePassword = useCallback(
    async (oldPassword: string, newPassword: string) => {
      const { data, error } = await api.POST("/api/auth/change-password", {
        body: { old_password: oldPassword, new_password: newPassword },
      });
      if (!data) throw new Error(errorMessage(error));
      accept(data);
    },
    [accept],
  );

  const value = useMemo(
    () => ({ status, user, login, demoLogin, logout, changePassword }),
    [status, user, login, demoLogin, logout, changePassword],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
