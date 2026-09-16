import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { useReport, type ReportAttempt, type ReportStudent } from "@/api/teacher";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime, formatTime } from "@/emulator/time";
import { cn } from "@/lib/utils";
import { SESSION_STATUS_TITLES, formatDeviation, formatDuration, formatScore, plural } from "@/teacher/labels";

const DECISION_TITLES: Record<string, string> = { accept: "Принята", reject: "Не принята" };

export function SessionReportPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  if (!sessionId) return <Navigate to="/teacher" replace />;
  return <Report sessionId={sessionId} />;
}

/** «Отчёт о занятии»: a row per trainee, expandable to attempts with a link to the review. */
function Report({ sessionId }: { sessionId: string }) {
  const query = useReport(sessionId);
  const [open, setOpen] = useState<Set<string>>(() => new Set());

  if (query.isPending) return <LoadingState text="Считаем отчёт…" />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const report = query.data;
  const summary = report.summary;

  function toggle(id: string) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="space-y-6">
      <div>
        <Link to={`/teacher/sessions/${sessionId}`} className="text-sm text-muted-foreground hover:underline">
          ← К занятию
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">Отчёт: {report.title}</h1>
        <p className="text-sm text-muted-foreground">
          {SESSION_STATUS_TITLES[report.status] ?? report.status}
          {report.started_at && ` · начато ${formatDateTime(report.started_at)}`}
          {report.finished_at && ` · завершено ${formatDateTime(report.finished_at)}`}
          {" · норматив "}
          {report.norm_seconds} с · порог {report.pass_threshold}
        </p>
        {report.status === "running" && (
          <p className="mt-1 text-sm text-warning-foreground">Занятие ещё идёт: цифры ниже — по уже закрытым карточкам.</p>
        )}
      </div>

      <section aria-label="Итоги" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Участвовали" value={`${summary.participated} из ${summary.students}`} />
        <Stat label="Карточек оценено" value={String(summary.evaluated)} />
        <Stat label="Зачтено" value={summary.evaluated ? `${summary.passed} (${Math.round((100 * summary.passed) / summary.evaluated)} %)` : "—"} />
        <Stat label="Средний балл" value={formatScore(summary.average)} />
      </section>

      {summary.typical_errors.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Типичные ошибки группы</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="flex flex-wrap gap-2 text-sm">
              {summary.typical_errors.map((e) => (
                <li key={e.code} className="rounded-md border px-2 py-1">
                  {e.title} <span className="text-muted-foreground">× {e.count}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="w-full min-w-[720px] text-sm" aria-label="Отчёт по обучающимся">
          <thead className="text-left text-xs text-muted-foreground">
            <tr className="border-b">
              <th className="w-8 py-2 pl-2" aria-label="Развернуть" />
              <th className="py-2 pr-3 font-medium">Обучающийся</th>
              <th className="py-2 pr-3 text-right font-medium">Попыток</th>
              <th className="py-2 pr-3 text-right font-medium">Средний балл</th>
              <th className="py-2 pr-3 text-right font-medium">Среднее время</th>
              <th className="py-2 pr-3 text-right font-medium">К нормативу</th>
              <th className="py-2 pr-3 text-right font-medium">Неверных решений</th>
              <th className="py-2 pr-3 font-medium">Типичные ошибки</th>
              <th className="py-2 pr-3 text-right font-medium">Грамотность</th>
            </tr>
          </thead>
          <tbody>
            {report.students.map((s) => (
              <Fragment key={s.student_id}>
                <StudentRow student={s} open={open.has(s.student_id)} onToggle={() => toggle(s.student_id)} threshold={report.pass_threshold} />
                {open.has(s.student_id) && (
                  <tr className="bg-muted/40">
                    <td />
                    <td colSpan={8} className="py-2 pr-3">
                      <AttemptsTable attempts={s.attempts} threshold={report.pass_threshold} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        Экспорт в PDF, XLSX и CSV и изменение оценки с причиной появятся в следующих версиях.
      </p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-card px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function StudentRow({ student, open, onToggle, threshold }: { student: ReportStudent; open: boolean; onToggle: () => void; threshold: number }) {
  const s = student;
  const low = s.average !== null && s.average < threshold;
  return (
    <tr className="border-b last:border-0" data-student={s.login}>
      <td className="py-2 pl-2">
        {s.attempts_total > 0 && (
          <button type="button" onClick={onToggle} className="rounded p-1 hover:bg-accent" aria-expanded={open} aria-label={`Попытки: ${s.full_name}`}>
            {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          </button>
        )}
      </td>
      <td className="py-2 pr-3">
        <div className="font-medium">{s.full_name}</div>
        <div className="text-xs text-muted-foreground">{s.login}</div>
      </td>
      <td className="py-2 pr-3 text-right tabular-nums">
        {s.attempts_total}
        {s.evaluated > 0 && <span className="text-xs text-muted-foreground"> · зачтено {s.passed}</span>}
      </td>
      <td className={cn("py-2 pr-3 text-right font-medium tabular-nums", low && "text-destructive")}>{formatScore(s.average)}</td>
      <td className="py-2 pr-3 text-right tabular-nums">{formatDuration(s.average_seconds)}</td>
      <td className={cn("py-2 pr-3 text-right tabular-nums", (s.average_deviation ?? 0) > 0 && "text-destructive")}>{formatDeviation(s.average_deviation)}</td>
      <td className={cn("py-2 pr-3 text-right tabular-nums", s.wrong_decisions > 0 && "text-destructive")}>{s.evaluated ? s.wrong_decisions : "—"}</td>
      <td className="py-2 pr-3">
        {s.typical_errors.length === 0 ? (
          <span className="text-muted-foreground">{s.evaluated ? "нет" : "—"}</span>
        ) : (
          s.typical_errors
            .slice(0, 3)
            .map((e) => `${e.title}${e.count > 1 ? ` ×${e.count}` : ""}`)
            .join(", ")
        )}
      </td>
      <td className="py-2 pr-3 text-right tabular-nums">{s.grammar_percent === null ? "—" : `${Math.round(s.grammar_percent)} %`}</td>
    </tr>
  );
}

function AttemptsTable({ attempts, threshold }: { attempts: ReportAttempt[]; threshold: number }) {
  return (
    <table className="w-full text-xs sm:text-sm" aria-label="Попытки">
      <thead className="text-left text-muted-foreground">
        <tr>
          <th className="py-1 pr-3 font-medium">Карточка</th>
          <th className="py-1 pr-3 font-medium">Выдана</th>
          <th className="py-1 pr-3 text-right font-medium">Балл</th>
          <th className="py-1 pr-3 text-right font-medium">Время</th>
          <th className="py-1 pr-3 font-medium">Решение</th>
          <th className="py-1 pr-3 font-medium">Ошибки</th>
          <th className="py-1 pr-3 font-medium">Разбор</th>
        </tr>
      </thead>
      <tbody>
        {attempts.map((a) => (
          <tr key={a.id} className="border-t border-border/60">
            <td className="py-1 pr-3">
              <span className="font-mono">{a.card_number}</span> {a.incident_title && <span className="text-muted-foreground">· {a.incident_title}</span>}
            </td>
            <td className="py-1 pr-3 whitespace-nowrap">{formatTime(a.issued_at)}</td>
            <td className="py-1 pr-3 text-right">
              {a.total === null ? (
                <span className="text-muted-foreground">{a.state === "issued" || a.state === "received" || a.state === "in_progress" ? "в работе" : "—"}</span>
              ) : (
                <Badge tone={a.passed ? "success" : a.total < threshold ? "danger" : "neutral"}>{formatScore(a.total)}</Badge>
              )}
            </td>
            <td className={cn("py-1 pr-3 text-right whitespace-nowrap tabular-nums", (a.deviation ?? 0) > 0 && "text-destructive")}>
              {a.seconds === null ? "—" : `${formatDuration(a.seconds)} (${formatDeviation(a.deviation)})`}
            </td>
            <td className="py-1 pr-3">
              {a.decision_expected === null ? (
                "—"
              ) : (
                <span className={cn(a.decision_correct === false && "text-destructive")}>
                  {a.decision_actual ? DECISION_TITLES[a.decision_actual] ?? a.decision_actual : "не принято"}
                  {a.decision_correct === false && ` (эталон: ${DECISION_TITLES[a.decision_expected] ?? a.decision_expected})`}
                </span>
              )}
            </td>
            <td className="py-1 pr-3">{a.errors.length === 0 ? <span className="text-muted-foreground">—</span> : a.errors.join(", ")}</td>
            <td className="py-1 pr-3">
              <Link to={`/teacher/attempts/${a.id}/review`} className="text-primary underline-offset-2 hover:underline">
                {a.total === null ? "Открыть" : "Разбор"}
              </Link>
            </td>
          </tr>
        ))}
      </tbody>
      {attempts.length > 0 && (
        <caption className="caption-bottom pt-1 text-left text-xs text-muted-foreground">
          {attempts.length} {plural(attempts.length, "попытка", "попытки", "попыток")}
        </caption>
      )}
    </table>
  );
}
