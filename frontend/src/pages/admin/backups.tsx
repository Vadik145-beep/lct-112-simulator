import { DatabaseBackup } from "lucide-react";

import { useBackups, useRequestBackup } from "@/api/admin";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/emulator/time";

const STATUS: Record<string, { title: string; tone: BadgeTone }> = {
  requested: { title: "в очереди", tone: "warning" },
  running: { title: "выполняется", tone: "warning" },
  done: { title: "готова", tone: "success" },
  failed: { title: "ошибка", tone: "danger" },
  removed: { title: "удалена по ротации", tone: "neutral" },
};
const KIND: Record<string, string> = { manual: "вручную", scheduled: "по расписанию" };

function formatSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

/** «Резервные копии» (PRD 14): the daily dumps of the backup service and «Сделать копию
 * сейчас»; the list refreshes every 5 s while a copy is in progress. */
export function AdminBackupsPage() {
  const backups = useBackups();
  const request = useRequestBackup();

  if (backups.isPending) return <LoadingState text="Читаем список копий…" />;
  if (backups.isError) return <ErrorState message={backups.error.message} onRetry={() => void backups.refetch()} />;
  const b = backups.data;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Резервные копии</h1>
          <p className="text-sm text-muted-foreground">
            Ежедневно в {b.schedule_time}, хранится {b.keep} последних · папка {b.folder} · служба копий{" "}
            {b.service_alive ? <Badge tone="success">работает</Badge> : <Badge tone="danger">не отвечает</Badge>}
          </p>
        </div>
        <Button onClick={() => request.mutate()} disabled={request.isPending || !b.service_alive}>
          <DatabaseBackup /> {request.isPending ? "Отправляем…" : "Сделать копию сейчас"}
        </Button>
      </div>
      {!b.service_alive && (
        <p className="text-sm text-destructive">Служба backup не отмечалась больше пяти минут: копию сейчас сделать нельзя. Проверьте контейнер backup.</p>
      )}
      {request.isError && <ErrorState message={request.error.message} />}
      <p className="text-xs text-muted-foreground">
        Восстановление проверяется командой <code>scripts/restore_test.sh</code>: копия разворачивается в отдельную базу, и вход в неё проверяется кодом приложения.
      </p>

      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="w-full min-w-[640px] text-sm" aria-label="Резервные копии">
          <thead className="text-left text-xs text-muted-foreground">
            <tr className="border-b">
              <th className="py-2 pl-3 pr-3 font-medium">Файл</th>
              <th className="py-2 pr-3 font-medium">Тип</th>
              <th className="py-2 pr-3 font-medium">Состояние</th>
              <th className="py-2 pr-3 text-right font-medium">Размер</th>
              <th className="py-2 pr-3 font-medium">Запрошена</th>
              <th className="py-2 pr-3 font-medium">Готова</th>
            </tr>
          </thead>
          <tbody>
            {b.items.length === 0 && (
              <tr>
                <td colSpan={6} className="py-6 text-center text-muted-foreground">
                  Копий пока нет: первая появится в {b.schedule_time} или по кнопке.
                </td>
              </tr>
            )}
            {b.items.map((item) => {
              const st = STATUS[item.status] ?? { title: item.status, tone: "neutral" as BadgeTone };
              return (
                <tr key={item.id ?? item.file_name ?? ""} className="border-b last:border-0" data-status={item.status}>
                  <td className="py-2 pl-3 pr-3 font-mono text-xs">{item.file_name ?? <span className="text-muted-foreground">ещё нет</span>}</td>
                  <td className="py-2 pr-3">{KIND[item.kind] ?? item.kind}</td>
                  <td className="py-2 pr-3">
                    <Badge tone={st.tone}>{st.title}</Badge>
                    {item.error && <div className="mt-1 max-w-md text-xs text-destructive">{item.error}</div>}
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">{formatSize(item.size_bytes)}</td>
                  <td className="py-2 pr-3 whitespace-nowrap">{formatDateTime(item.requested_at)}</td>
                  <td className="py-2 pr-3 whitespace-nowrap">{item.finished_at ? formatDateTime(item.finished_at) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
