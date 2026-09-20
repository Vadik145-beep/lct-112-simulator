import { ShieldCheck } from "lucide-react";
import { useState } from "react";

import { useAudit, useVerifyAudit } from "@/api/admin";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDateTime } from "@/emulator/time";
import { cn } from "@/lib/utils";

const PER_PAGE = 50;

/** «Журнал аудита» (PRD 13.7): filters by user, action and date; «Проверить целостность»
 * recomputes the hash chain on the server. */
export function AdminAuditPage() {
  const [login, setLogin] = useState("");
  const [action, setAction] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const query = useAudit({
    actor_login: login.trim(),
    action,
    date_from: from ? new Date(from).toISOString() : undefined,
    date_to: to ? new Date(`${to}T23:59:59`).toISOString() : undefined,
    page,
    per_page: PER_PAGE,
  });
  const verify = useVerifyAudit();

  const pages = query.data ? Math.max(1, Math.ceil(query.data.total / PER_PAGE)) : 1;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Журнал аудита</h1>
          <p className="text-sm text-muted-foreground">Каждая запись хранит хэш предыдущей: подмена или удаление ломают цепочку.</p>
        </div>
        <Button variant="outline" onClick={() => verify.mutate()} disabled={verify.isPending}>
          <ShieldCheck /> {verify.isPending ? "Проверяем…" : "Проверить целостность"}
        </Button>
      </div>

      {verify.data && (
        <div
          role="status"
          data-testid="audit-verify"
          className={cn("rounded-lg border p-3 text-sm", verify.data.ok ? "border-success/50 bg-success/10" : "border-destructive/50 bg-destructive/10")}
        >
          {verify.data.message}
        </div>
      )}
      {verify.isError && <ErrorState message={verify.error.message} />}

      <form
        className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
        aria-label="Фильтры"
        onSubmit={(e) => {
          e.preventDefault();
          setPage(1);
        }}
      >
        <div className="space-y-1.5">
          <Label htmlFor="audit-login">Пользователь (логин)</Label>
          <Input id="audit-login" value={login} onChange={(e) => setLogin(e.target.value)} placeholder="teacher1" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="audit-action">Действие</Label>
          <select id="audit-action" value={action} onChange={(e) => setAction(e.target.value)} className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm text-foreground">
            <option value="">Все</option>
            {(query.data?.actions ?? []).map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="audit-from">С даты</Label>
          <Input id="audit-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="audit-to">По дату</Label>
          <Input id="audit-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
      </form>

      {query.isPending ? (
        <LoadingState text="Читаем журнал…" />
      ) : query.isError ? (
        <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border bg-card">
            <table className="w-full min-w-[860px] text-sm" aria-label="Записи аудита">
              <thead className="text-left text-xs text-muted-foreground">
                <tr className="border-b">
                  <th className="py-2 pl-3 pr-3 font-medium">№</th>
                  <th className="py-2 pr-3 font-medium">Когда</th>
                  <th className="py-2 pr-3 font-medium">Кто</th>
                  <th className="py-2 pr-3 font-medium">Действие</th>
                  <th className="py-2 pr-3 font-medium">Объект</th>
                  <th className="py-2 pr-3 font-medium">Подробности</th>
                  <th className="py-2 pr-3 font-medium">IP</th>
                </tr>
              </thead>
              <tbody>
                {query.data.items.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-6 text-center text-muted-foreground">
                      Записей по фильтру нет.
                    </td>
                  </tr>
                )}
                {query.data.items.map((r) => (
                  <tr key={r.id} className="border-b align-top last:border-0">
                    <td className="py-1.5 pl-3 pr-3 font-mono text-xs text-muted-foreground">{r.id}</td>
                    <td className="py-1.5 pr-3 whitespace-nowrap">{formatDateTime(r.at)}</td>
                    <td className="py-1.5 pr-3">
                      {r.actor_login ?? <span className="text-muted-foreground">—</span>}
                      {r.actor_role && <span className="text-xs text-muted-foreground"> · {r.actor_role}</span>}
                    </td>
                    <td className="py-1.5 pr-3 font-mono text-xs">{r.action}</td>
                    <td className="py-1.5 pr-3 text-xs text-muted-foreground">
                      {r.entity}
                      {r.entity_id && <span className="block truncate font-mono">{r.entity_id}</span>}
                    </td>
                    <td className="max-w-md py-1.5 pr-3 text-xs">{r.details ? <code className="break-all">{JSON.stringify(r.details)}</code> : ""}</td>
                    <td className="py-1.5 pr-3 font-mono text-xs text-muted-foreground">{r.ip ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">
              Всего записей: {query.data.total} · страница {page} из {pages}
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                Назад
              </Button>
              <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
                Вперёд
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
