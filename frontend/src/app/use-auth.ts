import { useContext } from "react";

import { AuthContext, type AuthState } from "@/app/auth-context";

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth должен вызываться внутри AuthProvider");
  return ctx;
}
