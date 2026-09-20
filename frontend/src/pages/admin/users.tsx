import { Eye, KeyRound, Phone, Plus, ShieldBan, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";

import {
  useAdminServices,
  useAdminUsers,
  useCreateUser,
  useResetPassword,
  useRevealUser,
  useSipAccount,
  useUpdateUser,
  type AdminUserOut,
} from "@/api/admin";
import type { Role } from "@/api/client";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { formatDateTime } from "@/emulator/time";
import { ROLE_TITLES } from "@/lib/roles";
import { cn } from "@/lib/utils";

const ROLES: Role[] = ["student", "teacher", "admin"];

/** «Пользователи» (PRD 13.7): names hidden until «Показать» (the look is audited), creation
 * with a role and a service, blocking, password reset, the trainee's SIP account. */
export function AdminUsersPage() {
  const [role, setRole] = useState("");
  const [q, setQ] = useState("");
  const users = useAdminUsers({ role, q });
  const [creating, setCreating] = useState(false);
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [secret, setSecret] = useState<{ title: string; lines: string[] } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const reveal = useRevealUser();
  const update = useUpdateUser();
  const reset = useResetPassword();
  const sip = useSipAccount();

  const fail = (err: unknown) => setActionError(err instanceof Error ? err.message : "Не удалось выполнить действие.");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Пользователи</h1>
          <p className="text-sm text-muted-foreground">
            ФИО скрыто до нажатия «Показать»: каждый просмотр записывается в журнал аудита.
          </p>
        </div>
        <Button onClick={() => setCreating(true)} disabled={creating}>
          <Plus /> Создать пользователя
        </Button>
      </div>

      {creating && <CreateForm onDone={() => setCreating(false)} onCreated={(title, lines) => setSecret({ title, lines })} />}

      {secret && (
        <Card className="border-warning/60" role="status" data-testid="admin-secret">
          <CardHeader>
            <CardTitle className="text-base">{secret.title}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {secret.lines.map((line, i) => (
              <p key={i} className="font-mono">
                {line}
              </p>
            ))}
            <p className="text-xs text-muted-foreground">Показано один раз: передайте пользователю и закройте.</p>
            <Button size="sm" variant="outline" onClick={() => setSecret(null)}>
              Закрыть
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="users-role">Роль</Label>
          <Select id="users-role" value={role} onChange={(e) => setRole(e.target.value)} className="w-auto">
            <option value="">Все</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_TITLES[r]}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="users-q">Логин</Label>
          <Input id="users-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по логину" className="w-56" />
        </div>
      </div>

      {actionError && <ErrorState message={actionError} onRetry={() => setActionError(null)} />}

      {users.isPending ? (
        <LoadingState text="Загружаем пользователей…" />
      ) : users.isError ? (
        <ErrorState message={users.error.message} onRetry={() => void users.refetch()} />
      ) : (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="w-full min-w-[820px] text-sm" aria-label="Пользователи">
            <thead className="text-left text-xs text-muted-foreground">
              <tr className="border-b">
                <th className="py-2 pl-3 pr-3 font-medium">Логин</th>
                <th className="py-2 pr-3 font-medium">ФИО</th>
                <th className="py-2 pr-3 font-medium">Роль</th>
                <th className="py-2 pr-3 font-medium">Служба</th>
                <th className="py-2 pr-3 font-medium">Состояние</th>
                <th className="py-2 pr-3 font-medium">Последний вход</th>
                <th className="py-2 pr-3 font-medium">Действия</th>
              </tr>
            </thead>
            <tbody>
              {users.data.map((u) => (
                <UserRow
                  key={u.id}
                  user={u}
                  fullName={revealed[u.id] ?? undefined}
                  onReveal={() =>
                    reveal.mutate(u.id, {
                      onSuccess: (data) => setRevealed((prev) => ({ ...prev, [u.id]: data.full_name })),
                      onError: fail,
                    })
                  }
                  onBlock={(blocked) => update.mutate({ id: u.id, is_blocked: blocked }, { onError: fail })}
                  onReset={() =>
                    reset.mutate(u.id, {
                      onSuccess: (data) => setSecret({ title: `Временный пароль: ${u.login}`, lines: [`Пароль: ${data.temporary_password}`] }),
                      onError: fail,
                    })
                  }
                  onSip={() =>
                    sip.mutate(u.id, {
                      onSuccess: (data) =>
                        setSecret({
                          title: `SIP-учётка: ${u.login}`,
                          lines: [
                            `Логин: ${data.login}`,
                            `Пароль: ${data.password}`,
                            `Домен: ${data.domain} · WebSocket: wss://<хост>${data.ws_path}`,
                            data.telephony_enabled ? "Телефония включена." : "Телефония на этом стенде выключена: учётка заработает после включения профиля telephony.",
                          ],
                        }),
                      onError: fail,
                    })
                  }
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function UserRow({
  user: u,
  fullName,
  onReveal,
  onBlock,
  onReset,
  onSip,
}: {
  user: AdminUserOut;
  fullName?: string | undefined;
  onReveal: () => void;
  onBlock: (blocked: boolean) => void;
  onReset: () => void;
  onSip: () => void;
}) {
  return (
    <tr className={cn("border-b last:border-0", u.is_blocked && "opacity-60")} data-login={u.login}>
      <td className="py-2 pl-3 pr-3 font-mono">{u.login}</td>
      <td className="py-2 pr-3">
        {fullName ?? (
          <span className="inline-flex items-center gap-2">
            <span data-testid="user-display-name">{u.display_name}</span>
            <button type="button" onClick={onReveal} className="inline-flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline" aria-label={`Показать ФИО: ${u.login}`}>
              <Eye className="size-3" aria-hidden /> Показать
            </button>
          </span>
        )}
      </td>
      <td className="py-2 pr-3">{ROLE_TITLES[u.role]}</td>
      <td className="py-2 pr-3">{u.service_code ?? <span className="text-muted-foreground">—</span>}</td>
      <td className="py-2 pr-3">
        <div className="flex flex-wrap gap-1">
          {u.is_blocked ? <Badge tone="danger">заблокирован</Badge> : <Badge tone="success">активен</Badge>}
          {u.must_change_password && <Badge tone="warning">сменить пароль</Badge>}
          {u.has_sip_account && <Badge tone="neutral">SIP</Badge>}
        </div>
      </td>
      <td className="py-2 pr-3 whitespace-nowrap text-muted-foreground">{u.last_login_at ? formatDateTime(u.last_login_at) : "—"}</td>
      <td className="py-2 pr-3">
        <div className="flex flex-wrap gap-1">
          <Button size="sm" variant="outline" onClick={() => onBlock(!u.is_blocked)} aria-label={`${u.is_blocked ? "Разблокировать" : "Заблокировать"}: ${u.login}`}>
            {u.is_blocked ? <ShieldCheck /> : <ShieldBan />} {u.is_blocked ? "Разблокировать" : "Заблокировать"}
          </Button>
          <Button size="sm" variant="outline" onClick={onReset} aria-label={`Сбросить пароль: ${u.login}`}>
            <KeyRound /> Сбросить пароль
          </Button>
          {u.role === "student" && (
            <Button size="sm" variant="outline" onClick={onSip} aria-label={`SIP-учётка: ${u.login}`}>
              <Phone /> SIP-учётка
            </Button>
          )}
        </div>
      </td>
    </tr>
  );
}

function CreateForm({ onDone, onCreated }: { onDone: () => void; onCreated: (title: string, lines: string[]) => void }) {
  const create = useCreateUser();
  const services = useAdminServices();
  const [login, setLogin] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<Role>("student");
  const [service, setService] = useState("");
  const [password, setPassword] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      {
        login: login.trim(),
        full_name: fullName.trim(),
        role,
        service_code: service || null,
        password: password || null,
      },
      {
        onSuccess: (data) => {
          onCreated(`Пользователь создан: ${data.user.login}`, [`Временный пароль: ${data.temporary_password}`, "При первом входе пароль нужно сменить."]);
          onDone();
        },
      },
    );
  }

  return (
    <Card className="border-primary/40">
      <form onSubmit={submit} aria-label="Новый пользователь">
        <CardHeader>
          <CardTitle>Новый пользователь</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="new-login">Логин</Label>
              <Input id="new-login" required minLength={2} maxLength={64} pattern="[a-zA-Z0-9._\-]+" value={login} onChange={(e) => setLogin(e.target.value)} placeholder="латиница, цифры, точка, дефис" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-name">ФИО</Label>
              <Input id="new-name" required maxLength={200} value={fullName} onChange={(e) => setFullName(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-role">Роль</Label>
              <Select id="new-role" value={role} onChange={(e) => { setRole(e.target.value as Role); if (e.target.value !== "student") setService(""); }}>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {ROLE_TITLES[r]}
                  </option>
                ))}
              </Select>
            </div>
            {role === "student" ? (
              <div className="space-y-1.5">
                <Label htmlFor="new-service">Служба ДДС обучающегося</Label>
                <Select id="new-service" required value={service} onChange={(e) => setService(e.target.value)}>
                  <option value="">— выберите службу —</option>
                  {(services.data ?? []).map((s) => (
                    <option key={s.code} value={s.code}>
                      {s.short_title} · {s.code}
                    </option>
                  ))}
                </Select>
                <p className="text-xs text-muted-foreground">В журнале АРМ-112 обучающийся ставит статусы за эту службу.</p>
              </div>
            ) : (
              <p className="self-end pb-2 text-xs text-muted-foreground">Служба задаётся только обучающемуся ДДС.</p>
            )}
            <div className="space-y-1.5 md:col-span-2">
              <Label htmlFor="new-password">Временный пароль</Label>
              <Input id="new-password" minLength={8} maxLength={256} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="пусто — будет сгенерирован" />
            </div>
          </div>
          {create.isError && <ErrorState message={create.error.message} />}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "Создаём…" : "Создать пользователя"}
            </Button>
            <Button type="button" variant="outline" onClick={onDone}>
              Отмена
            </Button>
          </div>
        </CardContent>
      </form>
    </Card>
  );
}
