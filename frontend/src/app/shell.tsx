import { LogOut } from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "@/app/use-auth";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { ROLE_NAV, ROLE_TITLES } from "@/lib/roles";
import { cn } from "@/lib/utils";
import { SoftphoneBadge } from "@/softphone/call-panel";

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
          {/* Below lg the sections take a row of their own and wrap: no horizontal scroll
              inside the header at 390–1024 px (docs/BUGS.md, 5). */}
          <nav className="order-last flex w-full flex-wrap gap-1 lg:order-none lg:w-auto lg:flex-1" aria-label="Разделы">
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
            <SoftphoneBadge />
            <div className="min-w-0 max-w-[14rem] text-right leading-tight">
              <div className="truncate text-sm font-medium" title={user.full_name}>{user.full_name}</div>
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
