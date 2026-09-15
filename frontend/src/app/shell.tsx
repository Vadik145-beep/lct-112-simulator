import { LogOut } from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "@/app/use-auth";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { ROLE_NAV, ROLE_TITLES } from "@/lib/roles";
import { cn } from "@/lib/utils";

/** Common frame for all cabinets: header with user, role, theme and logout; role navigation. */
export function AppShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b bg-card">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <div className="flex items-center gap-2 font-semibold">
            <span className="rounded bg-primary px-1.5 py-0.5 font-mono text-xs text-primary-foreground">112</span>
            <span>Тренажёр ДДС</span>
          </div>
          <nav className="order-last flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto sm:flex-1" aria-label="Разделы">
            {ROLE_NAV[user.role].map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end
                className={({ isActive }) =>
                  cn(
                    "rounded-md px-3 py-1.5 text-sm whitespace-nowrap transition-colors hover:bg-accent",
                    isActive && "bg-accent font-medium",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <div className="text-right leading-tight">
              <div className="text-sm font-medium">{user.full_name}</div>
              <div className="text-xs text-muted-foreground">{ROLE_TITLES[user.role]}</div>
            </div>
            <ThemeToggle />
            <Button variant="outline" size="sm" onClick={handleLogout}>
              <LogOut /> Выйти
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
