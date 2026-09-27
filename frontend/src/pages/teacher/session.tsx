import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ClipboardList, Play, Settings, Square, WifiOff } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import {
  monitorKey,
  useClassifierTree,
  useFinishSession,
  useMonitor,
  useServices,
  useStartSession,
  useTeacherSession,
  type MonitorStudent,
  type SessionOut,
} from "@/api/teacher";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { acceptanceTimer, formatDateTime, formatSeconds, useNow } from "@/emulator/time";
import { useSessionEvents, type SessionEvent } from "@/emulator/ws";
import { cn } from "@/lib/utils";
import { CARDS_AT_ONCE, DIFFICULTY_TITLES, MODE_TITLES, RESPONSE_STATUS_TITLES, SESSION_STATUS_TITLES, formatScore, plural } from "@/teacher/labels";
import { STAGE_TITLES, applyEvent, summarize, withSnapshot, type MonitorState } from "@/teacher/monitor";

const STATUS_TONES: Record<string, BadgeTone> = { draft: "neutral", running: "success", finished: "primary" };
// How long after an `attempt.issued` event the snapshot is refetched for the card titles.
const TITLE_REFETCH_MS = 1500;

export function TeacherSessionPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  if (!sessionId) return <Navigate to="/teacher" replace />;
  return <SessionView sessionId={sessionId} />;
}

function SessionView({ sessionId }: { sessionId: string }) {
  const query = useTeacherSession(sessionId);
  if (query.isPending) return <LoadingState text="Загружаем занятие…" />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  return <SessionDetails session={query.data} />;
}

function SessionDetails({ session }: { session: SessionOut }) {
  const navigate = useNavigate();
  const start = useStartSession(session.id);
  const finish = useFinishSession(session.id);
  const [confirmFinish, setConfirmFinish] = useState(false);
  const live = session.status !== "draft";
  const error = start.error ?? finish.error;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to="/teacher" className="text-sm text-muted-foreground hover:underline">
            ← Занятия
          </Link>
          <h1 className="mt-1 flex flex-wrap items-center gap-2 text-2xl font-semibold">
            <span className="break-words">{session.title}</span>
            <Badge tone={STATUS_TONES[session.status]}>{SESSION_STATUS_TITLES[session.status] ?? session.status}</Badge>
          </h1>
          <p className="text-sm text-muted-foreground">
            {MODE_TITLES[session.mode] ?? session.mode} · {session.group_title || "без группы"} · {session.students}{" "}
            {plural(session.students, "обучающийся", "обучающихся", "обучающихся")}
            {session.started_at && ` · начато ${formatDateTime(session.started_at)}`}
            {session.finished_at && ` · завершено ${formatDateTime(session.finished_at)}`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {session.status === "draft" && (
            <>
              <Button variant="outline" onClick={() => navigate(`/teacher/sessions/${session.id}/edit`)}>
                <Settings /> Настройки
              </Button>
              <Button onClick={() => start.mutate()} disabled={start.isPending}>
                <Play /> {start.isPending ? "Запускаем…" : "Начать занятие"}
              </Button>
            </>
          )}
          {session.status === "running" &&
            (confirmFinish ? (
              <div className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-2 text-sm">
                <span>Открытые карточки будут закрыты и оценены как есть.</span>
                <Button variant="destructive" size="sm" onClick={() => finish.mutate(undefined, { onSuccess: () => setConfirmFinish(false) })} disabled={finish.isPending}>
                  {finish.isPending ? "Завершаем…" : "Да, завершить"}
                </Button>
                <Button variant="outline" size="sm" onClick={() => setConfirmFinish(false)}>
                  Отмена
                </Button>
              </div>
            ) : (
              <Button variant="destructive" onClick={() => setConfirmFinish(true)}>
                <Square /> Завершить занятие
              </Button>
            ))}
          {live && (
            <Button asChild variant={session.status === "finished" ? "default" : "outline"}>
              <Link to={`/teacher/sessions/${session.id}/report`}>
                <ClipboardList /> Отчёт
              </Link>
            </Button>
          )}
        </div>
      </div>

      {error && <ErrorState message={error.message} />}

      {live ? <Monitoring session={session} /> : <DraftOverview session={session} />}
    </div>
  );
}

/** How many cards each trainee gets: one, or three at difficulty 3 (older lessons: whatever
 * they were saved with; 0 meant the whole queue). */
function perStudent(session: SessionOut): number {
  return session.cards_per_student > 0 ? session.cards_per_student : 0;
}

function cardsText(session: SessionOut): string {
  const n = perStudent(session);
  if (n === 0) return "все подходящие";
  if (session.mode === "call_intake") return n === 1 ? "1 вызов на обучающегося" : `${n} ${plural(n, "вызов", "вызова", "вызовов")} подряд на обучающегося`;
  if (n === 1) return "1 карточка на обучающегося";
  const together = (CARDS_AT_ONCE[session.difficulty] ?? 1) >= n ? " сразу" : "";
  return `${n} ${plural(n, "карточка", "карточки", "карточек")}${together} на обучающегося`;
}

function SettingsSummary({ session }: { session: SessionOut }) {
  const tree = useClassifierTree();
  const groupTitles = useMemo(() => new Map((tree.data?.groups ?? []).map((g) => [g.code, g.title])), [tree.data]);
  const services = useServices();
  const serviceTitles = useMemo(() => new Map((services.data ?? []).map((s) => [s.code, s.short_title])), [services.data]);
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm">
      <dt className="text-muted-foreground">Сложность</dt>
      <dd>{DIFFICULTY_TITLES[session.difficulty] ?? session.difficulty}</dd>
      <dt className="text-muted-foreground">Норматив</dt>
      <dd>{session.mode === "call_intake" ? `${session.norm_seconds} с на вызов` : `${session.norm_seconds} с до первичного статуса`}</dd>
      <dt className="text-muted-foreground">Порог зачёта</dt>
      <dd>{session.pass_threshold} баллов</dd>
      <dt className="text-muted-foreground">Карточек</dt>
      <dd>{cardsText(session)}</dd>
      <dt className="text-muted-foreground">Подсказки</dt>
      <dd>{session.hints_enabled ? "включены" : "выключены (аттестация)"}</dd>
      {session.mode !== "call_intake" && (
        <>
          <dt className="text-muted-foreground">Службы</dt>
          <dd>{session.service_profile.length > 0 ? session.service_profile.map((c) => serviceTitles.get(c) ?? c).join(", ") : "все"}</dd>
        </>
      )}
      <dt className="text-muted-foreground">Группы происшествий</dt>
      <dd data-testid="session-groups">
        {session.incident_groups.length > 0 ? session.incident_groups.map((c) => groupTitles.get(c) ?? c).join("; ") : "все"}
      </dd>
    </dl>
  );
}

function DraftOverview({ session }: { session: SessionOut }) {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle>Настройки</CardTitle>
        </CardHeader>
        <CardContent>
          <SettingsSummary session={session} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Подходящие сценарии · {session.queue.length}</CardTitle>
        </CardHeader>
        <CardContent>
          {session.queue.length === 0 ? (
            <p className="text-sm text-destructive">
              Под выбранные настройки нет утверждённых сценариев. Утвердите сценарии в разделе «Сценарии» или измените настройки.
            </p>
          ) : (
            <ol className="list-decimal space-y-1 pl-5 text-sm">
              {session.queue.map((s) => (
                <li key={s.id}>
                  {s.title} <span className="text-muted-foreground">· сложность {s.difficulty}</span>
                </li>
              ))}
            </ol>
          )}
          {session.queue.length > 0 && perStudent(session) > 0 && (
            <p className="mt-2 text-xs text-muted-foreground">
              Каждый обучающийся получит {Math.min(perStudent(session), session.queue.length)} из них, сначала самые простые.
            </p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>
            {session.group_title || "Группа"} · {session.members.length}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {session.members.length === 0 ? (
            <p className="text-sm text-destructive">
              В группе нет обучающихся. <Link to="/teacher/groups" className="underline">Добавить</Link>
            </p>
          ) : (
            <ul className="space-y-1 text-sm">
              {session.members.map((m) => (
                <li key={m.id} className="flex justify-between gap-2">
                  <span>{m.full_name}</span>
                  <span className="text-muted-foreground">{m.login}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- monitoring

function Monitoring({ session }: { session: SessionOut }) {
  const client = useQueryClient();
  const query = useMonitor(session.id);
  const [state, setState] = useState<MonitorState | null>(null);
  const titleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // A fresh snapshot replaces the event-driven state (it already includes those events), but
  // not what the trainees are doing: that lives only in the events (docs/BUGS.md).
  useEffect(() => {
    const snapshot = query.data;
    if (snapshot) setState((prev) => withSnapshot(prev, snapshot));
  }, [query.data]);

  const onEvent = useCallback(
    (event: SessionEvent) => {
      setState((prev) => (prev ? applyEvent(prev, event) : prev));
      if (event.type === "attempt.issued") {
        if (titleTimer.current) clearTimeout(titleTimer.current);
        titleTimer.current = setTimeout(() => void client.invalidateQueries({ queryKey: monitorKey(session.id) }), TITLE_REFETCH_MS);
      }
      if (event.type === "session.finished") void client.invalidateQueries({ queryKey: ["teacher"] });
    },
    [client, session.id],
  );
  useEffect(() => () => {
    if (titleTimer.current) clearTimeout(titleTimer.current);
  }, []);
  const connection = useSessionEvents(session.id, query.data?.last_seq, onEvent);

  if (query.isPending || !state) return <LoadingState text="Собираем сводку по группе…" />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  return <MonitorBoard state={state} session={session} connection={connection} />;
}

function MonitorBoard({
  state,
  session,
  connection,
}: {
  state: MonitorState;
  session: SessionOut;
  connection: ReturnType<typeof useSessionEvents>;
}) {
  const now = useNow();
  const services = useServices();
  const serviceTitles = useMemo(() => new Map((services.data ?? []).map((s) => [s.code, s.short_title])), [services.data]);
  const snap = state.snapshot;
  const summaries = snap.students.map((s) => ({
    student: s,
    summary: summarize(s, snap.norm_seconds, snap.pass_threshold, now, snap.cards_total, state.stages[s.student_id], session.mode),
  }));
  const working = summaries.filter((s) => s.summary.status === "working" || s.summary.status === "waiting").length;
  const done = summaries.filter((s) => s.summary.status === "done").length;
  const overdue = summaries.filter((s) => s.summary.overdue).length;
  const totals = snap.students.filter((s) => s.average !== null);
  const average = totals.length ? totals.reduce((acc, s) => acc + (s.average ?? 0) * s.finished, 0) / totals.reduce((acc, s) => acc + s.finished, 0) : null;

  return (
    <div className="space-y-4">
      {connection === "offline" && (
        <div role="status" className="flex items-center gap-2 rounded-md border border-warning bg-warning/15 px-3 py-2 text-sm">
          <WifiOff className="size-4" aria-hidden /> Связь потеряна, переподключаемся…
        </div>
      )}
      <section aria-label="Сводка" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="В работе" value={String(working)} />
        <Stat label="Закончили" value={`${done} из ${snap.students.length}`} />
        <Stat label="Средний балл" value={formatScore(average === null ? null : Math.round(average * 10) / 10)} />
        <Stat label="Просрочек сейчас" value={String(overdue)} tone={overdue > 0 ? "danger" : "neutral"} />
      </section>
      {snap.students.length === 0 ? (
        <p className="text-sm text-muted-foreground">В группе нет обучающихся.</p>
      ) : (
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Обучающиеся">
          {summaries.map(({ student, summary }) => (
            <StudentTile
              key={student.student_id}
              student={student}
              summary={summary}
              stage={state.stages[student.student_id]}
              service={serviceTitles.get(student.service_code ?? "") ?? student.service_code ?? ""}
              norm={snap.norm_seconds}
              threshold={snap.pass_threshold}
              now={now}
              finished={session.status === "finished"}
              mode={session.mode}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "neutral" | "danger" }) {
  return (
    <div className={cn("rounded-lg border bg-card px-3 py-2", tone === "danger" && "border-destructive/50")}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("text-xl font-semibold tabular-nums", tone === "danger" && "text-destructive")}>{value}</div>
    </div>
  );
}

const TILE_STATUS: Record<string, string> = {
  idle: "ждёт карточку",
  waiting: "карточка не принята",
  working: "в работе",
  done: "все карточки закрыты",
};
const CALL_TILE_STATUS: Record<string, string> = {
  idle: "ждёт вызов",
  waiting: "входящий вызов",
  working: "принимает вызов",
  done: "все вызовы приняты",
};

function StudentTile({
  student,
  summary,
  stage,
  service,
  norm,
  threshold,
  now,
  finished,
  mode,
}: {
  student: MonitorStudent;
  summary: ReturnType<typeof summarize>;
  stage?: { stage: string; at: string } | undefined;
  service: string;
  norm: number;
  threshold: number;
  now: number;
  finished: boolean;
  mode?: string;
}) {
  const navigate = useNavigate();
  const current = summary.current;
  const call = mode === "call_intake";
  // A call's norm runs from the moment it was taken (received_at) until the card is saved.
  const timer = current
    ? call
      ? current.received_at
        ? acceptanceTimer(current.received_at, null, norm, now)
        : null
      : acceptanceTimer(current.issued_at, current.primary_status_at, norm, now)
    : null;
  const targetId = current?.attempt_id ?? student.last_attempt_id;
  const alert = summary.overdue || (current?.card_status === "not_notified") || (current?.card_status === "not_finished");
  const statusText = finished ? (student.finished > 0 ? "занятие завершено" : "не участвовал") : (call ? CALL_TILE_STATUS : TILE_STATUS)[summary.status];
  const stageText = !finished && stage && summary.status !== "idle" ? STAGE_TITLES[stage.stage] ?? stage.stage : null;

  return (
    <li>
      <button
        type="button"
        data-student={student.login}
        data-status={summary.status}
        disabled={!targetId}
        onClick={() => targetId && navigate(`/teacher/attempts/${targetId}/review`)}
        className={cn(
          "flex w-full flex-col gap-2 rounded-lg border bg-card p-3 text-left transition-colors",
          targetId ? "hover:bg-accent/40" : "cursor-default",
          alert && "border-destructive/60 bg-destructive/5",
          !alert && summary.lowScore && "border-warning",
        )}
        aria-label={`${student.full_name}: ${statusText}`}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate font-medium">{student.full_name}</div>
            <div className="truncate text-xs text-muted-foreground">
              {service || "служба не задана"} · {student.login}
            </div>
          </div>
          {alert && <AlertTriangle className="size-4 shrink-0 text-destructive" aria-label="Превышение норматива" />}
        </div>
        <div className="text-sm">
          <span data-role="tile-status">{stageText ?? statusText}</span>
          {current && (
            <span className="text-muted-foreground"> · № {current.card_number}</span>
          )}
          {student.active.length > 1 && (
            <span className="text-muted-foreground">
              {" "}
              · ещё {student.active.length - 1} {call ? "в очереди" : "в журнале"}
            </span>
          )}
        </div>
        {current && timer && (
          <div className="flex items-center gap-2 text-sm">
            <span
              className={cn(
                "rounded-md px-2 py-0.5 font-mono tabular-nums",
                timer.phase === "ok" && "bg-muted",
                timer.phase === "warning" && "bg-warning/25",
                (timer.phase === "overdue" || timer.phase === "late") && "bg-destructive/15 text-destructive",
                timer.phase === "done" && "bg-success/15 text-success",
              )}
              title={call ? "Идёт разговор и заполнение карточки" : current.primary_status_at ? "Время до первичного статуса" : "Идёт норматив принятия"}
            >
              {formatSeconds(timer.elapsed)}
            </span>
            <span className="text-xs text-muted-foreground">{!call && current.primary_status_at ? RESPONSE_STATUS_TITLES[current.response_status] ?? current.response_status_title ?? current.response_status : `норматив ${norm} с`}</span>
          </div>
        )}
        <div className="mt-auto flex items-center justify-between text-xs text-muted-foreground">
          <span>
            закрыто {student.finished}
            {student.finished > 0 && `, зачтено ${student.passed}`}
            {student.average !== null && ` · средний ${formatScore(student.average)}`}
          </span>
          {student.last_total !== null && (
            <Badge tone={student.last_total < threshold ? "danger" : "success"} data-role="last-score">
              {formatScore(student.last_total)}
            </Badge>
          )}
        </div>
      </button>
    </li>
  );
}
