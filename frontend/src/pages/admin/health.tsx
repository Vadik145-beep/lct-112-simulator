import { Activity, BellRing, Check } from "lucide-react";

import { useAcknowledge, useAdminHealth, useNotifications, type ServiceTile } from "@/api/admin";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime, formatTime } from "@/emulator/time";
import { cn } from "@/lib/utils";

const STATUS: Record<string, { title: string; tone: "success" | "danger" | "neutral" }> = {
  ok: { title: "работает", tone: "success" },
  down: { title: "не отвечает", tone: "danger" },
  off: { title: "не настроено", tone: "neutral" },
};

/** «Состояние системы» (PRD 13.7): a tile per service, the load, lessons and calls in
 * progress, and the notifications to acknowledge. Refreshes every 15 s. */
export function AdminHealthPage() {
  const health = useAdminHealth();
  const notifications = useNotifications();
  const ack = useAcknowledge();

  // The heading is shown at once: the first poll of the services takes a few seconds.
  if (health.isPending || health.isError) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold">Состояние системы</h1>
        {health.isError ? (
          <ErrorState message={health.error.message} onRetry={() => void health.refetch()} />
        ) : (
          <LoadingState text="Опрашиваем сервисы…" />
        )}
      </div>
    );
  }
  const h = health.data;
  const down = h.services.filter((s) => s.status === "down");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Состояние системы</h1>
          <p className="text-sm text-muted-foreground">
            Проверено {formatTime(h.checked_at)} · версия {h.version} · обновляется каждые 15 секунд.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void health.refetch()} disabled={health.isFetching}>
          <Activity /> {health.isFetching ? "Проверяем…" : "Проверить сейчас"}
        </Button>
      </div>

      {down.length > 0 && (
        <div role="alert" className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm" data-testid="health-alert">
          Не отвечают: {down.map((s) => s.title).join(", ")}. Оценка и занятия продолжают работать без недоступных провайдеров; проверьте контейнеры.
        </div>
      )}

      <section aria-label="Сервисы" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {h.services.map((s) => (
          <Tile key={s.name} tile={s} />
        ))}
      </section>

      <section aria-label="Нагрузка и активность" className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Процессор" value={h.load.cpu_percent === null ? "—" : `${h.load.cpu_percent} %`} hint={h.load.cpu_count ? `${h.load.cpu_count} ядер` : undefined} />
        <Stat
          label="Память"
          value={h.load.memory_used_mb === null || h.load.memory_total_mb === null ? "—" : `${Math.round(h.load.memory_used_mb / 1024)} / ${Math.round(h.load.memory_total_mb / 1024)} ГБ`}
        />
        <Stat label="Загрузка (1 мин)" value={h.load.load_1 === null ? "—" : String(h.load.load_1)} />
        <Stat label="Идут занятия" value={String(h.running_sessions)} />
        <Stat label="Активные звонки" value={String(h.active_calls)} />
        <Stat label="Карточек в работе" value={String(h.active_attempts)} />
      </section>

      <Card data-testid="notifications">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <BellRing className="size-4" aria-hidden /> Оповещения {h.open_notifications > 0 && <Badge tone="danger">{h.open_notifications}</Badge>}
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {notifications.isPending ? (
            <LoadingState text="Загружаем…" />
          ) : notifications.isError ? (
            <ErrorState message={notifications.error.message} onRetry={() => void notifications.refetch()} />
          ) : notifications.data.length === 0 ? (
            <p className="text-muted-foreground">Неподтверждённых оповещений нет.</p>
          ) : (
            <ul className="space-y-2">
              {notifications.data.map((n) => (
                <li key={n.id} className="flex flex-wrap items-start justify-between gap-3 rounded-md border p-3" data-source={n.source}>
                  <div>
                    <div className="font-medium">{n.title}</div>
                    <div className="text-xs text-muted-foreground">{formatDateTime(n.at)}</div>
                    <p className="mt-1 text-muted-foreground">{n.message}</p>
                  </div>
                  <Button size="sm" variant="outline" onClick={() => ack.mutate(n.id)} disabled={ack.isPending} aria-label={`Подтвердить: ${n.title}`}>
                    <Check /> Подтвердить
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {ack.isError && <ErrorState message={ack.error.message} />}
        </CardContent>
      </Card>
    </div>
  );
}

function Tile({ tile }: { tile: ServiceTile }) {
  const s = STATUS[tile.status] ?? { title: tile.status, tone: "neutral" as const };
  return (
    <div className={cn("rounded-lg border bg-card p-3", tile.status === "down" && "border-destructive/60")} data-service={tile.name} data-status={tile.status}>
      <div className="flex items-start justify-between gap-2">
        <div className="text-sm font-medium">{tile.title}</div>
        <Badge tone={s.tone}>{s.title}</Badge>
      </div>
      <div className="mt-1 truncate text-xs text-muted-foreground" title={tile.detail ?? undefined}>
        {tile.latency_ms !== null && tile.latency_ms !== undefined && tile.status === "ok" ? `${tile.latency_ms} мс` : tile.detail ?? ""}
      </div>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string | undefined }) {
  return (
    <div className="rounded-lg border bg-card px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}
