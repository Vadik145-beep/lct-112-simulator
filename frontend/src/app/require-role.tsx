import { Navigate, Outlet, useLocation } from "react-router-dom";

import type { Role } from "@/api/client";
import { useAuth } from "@/app/use-auth";
import { FullScreenLoading } from "@/components/states";
import { ForbiddenPage } from "@/pages/forbidden";

/** Guards a route subtree: anonymous users go to /login, wrong roles see "Нет доступа". */
export function RequireRole({ roles }: { roles: Role[] }) {
  const { status, user } = useAuth();
  const location = useLocation();

  if (status === "loading") return <FullScreenLoading />;
  if (status === "anonymous" || !user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (user.must_change_password && location.pathname !== "/change-password") {
    return <Navigate to="/change-password" replace />;
  }
  if (!roles.includes(user.role)) return <ForbiddenPage />;
  return <Outlet />;
}
