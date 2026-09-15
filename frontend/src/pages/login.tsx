import { useQuery } from "@tanstack/react-query";
import { GraduationCap, Shield, UserRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { api, type Role } from "@/api/client";
import { useAuth } from "@/app/use-auth";
import { FullScreenLoading } from "@/components/states";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ROLE_HOME } from "@/lib/roles";

const DEMO_BUTTONS: { role: Role; label: string; icon: typeof UserRound }[] = [
  { role: "student", label: "Войти как обучающийся", icon: UserRound },
  { role: "teacher", label: "Войти как преподаватель", icon: GraduationCap },
  { role: "admin", label: "Войти как администратор", icon: Shield },
];

export function LoginPage() {
  const { status, user, login, demoLogin } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [loginValue, setLoginValue] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"form" | Role | null>(null);

  const config = useQuery({
    queryKey: ["config"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/config");
      if (!data) throw error;
      return data;
    },
    staleTime: Infinity,
  });

  // Do not offer the form while a previous session may still be restored.
  if (status === "loading") return <FullScreenLoading />;
  if (status === "authenticated" && user) {
    // Return to the page the user came from only if it belongs to their own cabinet.
    const from = (location.state as { from?: string } | null)?.from;
    const home = ROLE_HOME[user.role];
    return <Navigate to={from && from.startsWith(home) ? from : home} replace />;
  }

  async function run(action: () => Promise<void>, kind: "form" | Role) {
    setBusy(kind);
    setError(null);
    try {
      await action();
      navigate("/", { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось войти. Попробуйте ещё раз.");
    } finally {
      setBusy(null);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void run(() => login(loginValue, password), "form");
  }

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-6 px-4 py-8">
      <div className="absolute top-3 right-3">
        <ThemeToggle />
      </div>
      <div className="text-center">
        <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-xl bg-primary font-mono text-lg font-bold text-primary-foreground">
          112
        </div>
        <h1 className="text-2xl font-semibold">Тренажёр оператора ДДС</h1>
        <p className="mt-1 text-sm text-muted-foreground">Учебная система. Реальные вызовы не обрабатываются.</p>
      </div>

      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Вход</CardTitle>
          <CardDescription>Введите логин и пароль, выданные администратором.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="login">Логин</Label>
              <Input
                id="login"
                name="login"
                autoComplete="username"
                autoFocus
                required
                value={loginValue}
                onChange={(e) => setLoginValue(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Пароль</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {error && (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={busy !== null || !loginValue || !password}>
              {busy === "form" ? "Входим…" : "Войти"}
            </Button>
          </form>

          {config.data?.demo_mode && (
            <div className="mt-6 space-y-2 border-t pt-4">
              <p className="text-xs text-muted-foreground">Демо-режим: вход без пароля</p>
              {DEMO_BUTTONS.map(({ role, label, icon: Icon }) => (
                <Button
                  key={role}
                  type="button"
                  variant="secondary"
                  className="w-full justify-start"
                  disabled={busy !== null}
                  onClick={() => void run(() => demoLogin(role), role)}
                >
                  <Icon /> {busy === role ? "Входим…" : label}
                </Button>
              ))}
            </div>
          )}
          {config.isError && (
            <p className="mt-4 text-xs text-muted-foreground">
              Сервер не ответил на запрос настроек. Обычный вход по логину и паролю доступен.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
