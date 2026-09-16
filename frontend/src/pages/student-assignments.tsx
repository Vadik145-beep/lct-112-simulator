import { ArrowRight, ClipboardList } from "lucide-react";
import { Link } from "react-router-dom";

import { useAssignments, type AssignmentOut } from "@/api/training";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/emulator/time";

const MODE_TITLES: Record<string, string> = {
  card_response: "Реагирование на карточку",
  call_intake: "Приём вызова",
};

const STATUS_TITLES: Record<string, string> = {
  running: "идёт сейчас",
  draft: "ещё не началось",
  finished: "завершено",
};

/** «Мои задания»: the running session first and large, the rest as a list. */
export function StudentAssignmentsPage() {
  const query = useAssignments();

  if (query.isPending) return <LoadingState text="Загружаем задания…" />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;

  const active = query.data.find((a) => a.status === "running");
  const rest = query.data.filter((a) => a !== active);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Мои задания</h1>
        <p className="text-sm text-muted-foreground">Занятия, которые назначил преподаватель вашей группе.</p>
      </div>

      {active ? (
        <Card className="border-primary/40">
          <CardHeader>
            <CardDescription>Активное занятие · {MODE_TITLES[active.mode] ?? active.mode}</CardDescription>
            <CardTitle className="text-xl">{active.title}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end justify-between gap-4">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Служба</dt>
              <dd>{active.service?.title ?? "—"}</dd>
              <dt className="text-muted-foreground">Норматив</dt>
              <dd>{active.norm_seconds} с на первичный статус</dd>
              <dt className="text-muted-foreground">Карточки</dt>
              <dd>
                в работе {active.active_cards}, закрыто {active.finished_cards}
              </dd>
              {active.started_at && (
                <>
                  <dt className="text-muted-foreground">Начато</dt>
                  <dd>{formatDateTime(active.started_at)}</dd>
                </>
              )}
            </dl>
            <Button asChild size="lg">
              <Link to={`/student/sessions/${active.id}/journal`}>
                Открыть журнал АРМ-112 <ArrowRight />
              </Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
            <ClipboardList className="size-8" aria-hidden />
            <p>Сейчас активных занятий нет. Когда преподаватель запустит занятие, оно появится здесь.</p>
          </CardContent>
        </Card>
      )}

      {rest.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-lg font-medium">Другие занятия</h2>
          <ul className="divide-y rounded-lg border bg-card">
            {rest.map((a) => (
              <AssignmentRow key={a.id} assignment={a} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function AssignmentRow({ assignment }: { assignment: AssignmentOut }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm">
      <div>
        <div className="font-medium">{assignment.title}</div>
        <div className="text-muted-foreground">
          {MODE_TITLES[assignment.mode] ?? assignment.mode} · {STATUS_TITLES[assignment.status] ?? assignment.status}
          {assignment.finished_cards > 0 ? ` · закрыто карточек: ${assignment.finished_cards}` : ""}
        </div>
      </div>
      {assignment.status !== "draft" && (
        <Button asChild variant="outline" size="sm">
          <Link to={`/student/sessions/${assignment.id}/journal`}>Журнал</Link>
        </Button>
      )}
    </li>
  );
}
