import { createContext } from "react";

import type { Role, UserOut } from "@/api/client";

export type Status = "loading" | "anonymous" | "authenticated";

export interface AuthState {
  status: Status;
  user: UserOut | null;
  login: (login: string, password: string) => Promise<void>;
  demoLogin: (role: Role) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (oldPassword: string, newPassword: string) => Promise<void>;
}

export const AuthContext = createContext<AuthState | null>(null);
