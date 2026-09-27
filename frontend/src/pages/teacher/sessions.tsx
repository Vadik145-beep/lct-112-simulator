import { CalendarDays, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { useDeleteSessions, useTeacherSessions, type SessionListItem } from "@/api/teacher";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { formatDate, formatTime } from "@/emulator/time";
import { MODE_TITLES, SESSION_STATUS_TITLES, formatScore } from "@/teacher/labels";

const STATUS_TONES: Record<string, BadgeTone> = { draft: "neutral", running: "success", finished: "primary" };

/** «Занятия»: the teacher's sessions, the running one first. */
export function TeacherSessionsPage() {
  const query = useTeacherSessions();
  const remove = useDeleteSessions();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);

  if (query.isPending) return <LoadingState text="Загружаем занятия…" />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;

  const order: Record<string, number> = { running: 0, draft: 1, finished: 2 };
  const sessions = [...query.data].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3));
  // A running lesson is finished first, then deleted.
  const deletable = sessions.filter((s) => s.status !== "running");
  const chosen = deletable.filter((s) => selected.has(s.id));
  const allChosen = deletable.length > 0 && chosen.length === deletable.length;
  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const doRemove = () =>
    remove.mutate(
      chosen.map((s) => s.id),
      {
        onSuccess: () => {
          setSelected(new Set());
          setConfirming(false);
        },
      },
    );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Занятия</h1>
          <p className="text-sm text-muted-foreground">Создайте занятие, запустите его и следите за группой вживую.</p>
        </div>
        <Button asChild>
          <Link to="/teacher/sessions/new">
            <Plus /> Создать занятие
          </Link>
        </Button>
      </div>

      {sessions.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
            <CalendarDays className="size-8" aria-hidden />
            <p>Занятий пока нет. Нажмите «Создать занятие», чтобы назначить карточки группе.</p>
          </CardContent>
        </Card>
      ) : (
        <>
          {chosen.length > 0 && (
            <div className="hidden flex-wrap items-center gap-3 md:flex" data-testid="sessions-selection">
              <span className="text-sm text-muted-foreground">Выбрано: {chosen.length}</span>
              <Button size="sm" variant="outline" onClick={() => setConfirming(true)} disabled={remove.isPending}>
                <Trash2 /> Удалить выбранные
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
                Снять выбор
              </Button>
            </div>
          )}
          {confirming && chosen.length > 0 && (
            <div
              className="hidden flex-wrap items-center gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm md:flex"
              role="alertdialog"
              aria-label="Удаление занятий"
            >
              <span>
                Удалить занятия ({chosen.length})? Карточки, оценки, комментарии и записи разговоров этих
                занятий удалятся безвозвратно.
              </span>
              <Button size="sm" variant="destructive" onClick={doRemove} disabled={remove.isPending}>
                {remove.isPending ? "Удаляем…" : "Да, удалить"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                Отмена
              </Button>
            </div>
          )}
          {remove.isError && (
            <p className="text-sm text-destructive" role="alert">
              Не удалось удалить: {remove.error.message}
            </p>
          )}
          <table className="hidden w-full text-sm md:table" aria-label="Список занятий">
            <thead className="text-left text-xs text-muted-foreground">
              <tr className="border-b">
                <th className="w-8 py-2 pr-2">
                  <input
                    type="checkbox"
                    aria-label="Выбрать все занятия"
                    checked={allChosen}
                    disabled={deletable.length === 0}
                    onChange={() => setSelected(allChosen ? new Set() : new Set(deletable.map((s) => s.id)))}
                  />
                </th>
                <th className="py-2 pr-3 font-medium">Дата</th>
                <th className="py-2 pr-3 font-medium">Занятие</th>
                <th className="py-2 pr-3 font-medium">Группа</th>
                <th className="py-2 pr-3 font-medium">Режим</th>
                <th className="py-2 pr-3 font-medium">Статус</th>
                <th className="py-2 pr-3 text-right font-medium">Средний балл</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id} className="border-b last:border-0 hover:bg-accent/40">
                  <td className="py-2 pr-2">
                    <input
                      type="checkbox"
                      aria-label={`Выбрать: ${s.title}`}
                      checked={selected.has(s.id)}
                      disabled={s.status === "running"}
                      title={s.status === "running" ? "Занятие идёт: завершите его, потом удаляйте" : undefined}
                      onChange={() => toggle(s.id)}
                    />
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap text-muted-foreground">{when(s)}</td>
                  <td className="py-2 pr-3">
                    <Link to={`/teacher/sessions/${s.id}`} className="font-medium hover:underline">
                      {s.title}
                    </Link>
                  </td>
                  <td className="py-2 pr-3">{s.group_title || "—"}</td>
                  <td className="py-2 pr-3">{MODE_TITLES[s.mode] ?? s.mode}</td>
                  <td className="py-2 pr-3">
                    <Badge tone={STATUS_TONES[s.status]}>{SESSION_STATUS_TITLES[s.status] ?? s.status}</Badge>
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">
                    {formatScore(s.average)}
                    {s.evaluated > 0 && <span className="text-xs text-muted-foreground"> · {s.evaluated} карт.</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="space-y-2 md:hidden" aria-label="Список занятий">
            {sessions.map((s) => (
              <li key={s.id}>
                <Link to={`/teacher/sessions/${s.id}`} className="block rounded-lg border bg-card p-3 hover:bg-accent/40">
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-medium">{s.title}</span>
                    <Badge tone={STATUS_TONES[s.status]}>{SESSION_STATUS_TITLES[s.status] ?? s.status}</Badge>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {when(s)} · {s.group_title || "без группы"} · {MODE_TITLES[s.mode] ?? s.mode}
                  </div>
                  <div className="mt-1 text-sm">Средний балл: {formatScore(s.average)}</div>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function when(s: SessionListItem): string {
  const at = s.started_at ?? s.created_at;
  return `${formatDate(at)} ${formatTime(at, false)}`;
}
