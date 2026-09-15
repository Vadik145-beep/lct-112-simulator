import { Navigate } from "react-router-dom";

import { useAuth } from "@/app/use-auth";
import { FullScreenLoading } from "@/components/states";
import { ROLE_HOME } from "@/lib/roles";

/** "/" sends the user to the cabinet of their role (or to the login page). */
export function HomePage() {
  const { status, user } = useAuth();
  if (status === "loading") return <FullScreenLoading />;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={ROLE_HOME[user.role]} replace />;
}
