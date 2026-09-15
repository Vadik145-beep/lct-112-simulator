import { ShieldOff } from "lucide-react";
import { Link } from "react-router-dom";

import { useAuth } from "@/app/use-auth";
import { Button } from "@/components/ui/button";
import { ROLE_HOME } from "@/lib/roles";

export function ForbiddenPage() {
  const { user } = useAuth();
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4 px-4 text-center">
      <ShieldOff className="size-10 text-muted-foreground" aria-hidden />
      <h1 className="text-2xl font-semibold">Нет доступа</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        Этот раздел недоступен для вашей роли. Если вам нужен доступ, обратитесь к администратору.
      </p>
      <Button asChild>
        <Link to={user ? ROLE_HOME[user.role] : "/login"}>Перейти в свой кабинет</Link>
      </Button>
    </div>
  );
}
