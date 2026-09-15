import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Routes } from "react-router-dom";

import type { UserOut } from "@/api/client";
import { AuthContext, type AuthState } from "@/app/auth-context";

export function makeUser(overrides: Partial<UserOut> = {}): UserOut {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    login: "student1",
    full_name: "Кузнецов Обучающийся 1",
    role: "student",
    service_code: null,
    must_change_password: false,
    ...overrides,
  };
}

export function makeAuth(overrides: Partial<AuthState> = {}): AuthState {
  return {
    status: "anonymous",
    user: null,
    login: vi.fn(),
    demoLogin: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    ...overrides,
  };
}

/** Renders `element` at `path` inside router, query and auth providers. */
type Entry = string | { pathname: string; state?: unknown };

export function renderAt(element: ReactElement, path: Entry, auth: AuthState, extraRoutes?: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={auth}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            {element}
            {extraRoutes}
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}
