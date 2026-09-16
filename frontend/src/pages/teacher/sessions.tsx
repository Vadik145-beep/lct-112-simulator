import { CalendarDays, Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { useTeacherSessions, type SessionListItem } from "@/api/teacher";
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

  if (query.isPending) return <LoadingState text="Загружаем занятия…" />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;

  const order: Record<string, number> = { running: 0, draft: 1, finished: 2 };
  const sessions = [...query.data].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3));

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
          <table className="hidden w-full text-sm md:table" aria-label="Список занятий">
            <thead className="text-left text-xs text-muted-foreground">
              <tr className="border-b">
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
