import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { useAuth } from "@/app/use-auth";
import { FullScreenLoading } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ROLE_HOME } from "@/lib/roles";

const MIN_LENGTH = 8;

export function ChangePasswordPage() {
  const { status, user, changePassword } = useAuth();
  const navigate = useNavigate();
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status === "loading") return <FullScreenLoading />;
  if (status === "anonymous" || !user) return <Navigate to="/login" replace />;

  const mismatch = repeat.length > 0 && newPassword !== repeat;
  const tooShort = newPassword.length > 0 && newPassword.length < MIN_LENGTH;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!user || mismatch || tooShort) return;
    setBusy(true);
    setError(null);
    try {
      await changePassword(oldPassword, newPassword);
      navigate(ROLE_HOME[user.role], { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сменить пароль.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4 py-8">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Смена пароля</CardTitle>
          <CardDescription>
            {user.must_change_password
              ? "Перед началом работы задайте свой пароль."
              : "Введите текущий пароль и новый пароль."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="old">Текущий пароль</Label>
              <Input id="old" type="password" autoComplete="current-password" autoFocus value={oldPassword} onChange={(e) => setOldPassword(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new">Новый пароль (не короче {MIN_LENGTH} символов)</Label>
              <Input id="new" type="password" autoComplete="new-password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
              {tooShort && <p className="text-xs text-destructive">Слишком короткий пароль.</p>}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="repeat">Новый пароль ещё раз</Label>
              <Input id="repeat" type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
              {mismatch && <p className="text-xs text-destructive">Пароли не совпадают.</p>}
            </div>
            {error && (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={busy || !oldPassword || !newPassword || !repeat || mismatch || tooShort}>
              {busy ? "Сохраняем…" : "Сменить пароль"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
