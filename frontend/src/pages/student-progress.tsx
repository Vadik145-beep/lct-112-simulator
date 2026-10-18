import { BookOpen, TrendingUp } from "lucide-react";
import { Fragment } from "react";
import { Link } from "react-router-dom";

import { useProgress, type ProgressOut } from "@/api/review";
import { ErrorState, LoadingState } from "@/components/states";
import { AdviceList } from "@/components/advice-list";
import { StreamSummaryView } from "@/components/stream-summary";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/emulator/time";
import { cn } from "@/lib/utils";
import { MODE_TITLES, SESSION_STATUS_TITLES, formatDeviation, formatDuration, formatScore, plural } from "@/teacher/labels";

// The start of every skill rating (PRD 9.7) and the band around it that reads «средне».
const START_RATING = 1400;
const RATING_BAND = 25;

/** «Мой прогресс» (PRD 13.7): the score per lesson with its advice, time against the norm,
 * frequent errors and what to read, and at the bottom how each incident group goes. */
export function StudentProgressPage() {
  const query = useProgress();
  if (query.isPending) return <LoadingState text="Считаем прогресс…" />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const p = query.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Мой прогресс</h1>
        <p className="text-sm text-muted-foreground">
          Баллы и советы по занятиям, время против норматива, частые ошибки и что повторить.
          {p.demo_data && (
            <Badge tone="warning" className="ml-2">
              демонстрационные данные
            </Badge>
          )}
        </p>
      </div>

      <section aria-label="Итоги" className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="progress-stats">
        <Stat label="Карточек" value={String(p.attempts)} />
        <Stat label="Оценено" value={String(p.evaluated)} />
        <Stat label="Зачтено" value={p.evaluated ? `${p.passed} (${Math.round((100 * p.passed) / p.evaluated)} %)` : "—"} />
        <Stat label="Средний балл" value={formatScore(p.average)} />
      </section>

      {p.sessions.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-muted-foreground">
            Занятий с результатами пока нет. Когда преподаватель проведёт занятие, здесь появятся баллы.
          </CardContent>
        </Card>
      ) : (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="w-full min-w-[640px] text-sm" aria-label="Занятия">
            <thead className="text-left text-xs text-muted-foreground">
              <tr className="border-b">
                <th className="py-2 pl-3 pr-3 font-medium">Занятие</th>
                <th className="py-2 pr-3 font-medium">Когда</th>
                <th className="py-2 pr-3 text-right font-medium">Попыток</th>
                <th className="py-2 pr-3 text-right font-medium">Средний балл</th>
                <th className="py-2 pr-3 text-right font-medium">Время</th>
                <th className="py-2 pr-3 text-right font-medium">К нормативу</th>
                <th className="py-2 pr-3 font-medium">Отметки</th>
              </tr>
            </thead>
            <tbody>
              {p.sessions.map((s) => {
                const low = s.average !== null && s.average < s.pass_threshold;
                return (
                  <Fragment key={s.session_id}>
                    <tr className={cn("border-b last:border-0", (s.stream || s.advice.length > 0) && "border-b-0")}>
                      <td className="py-2 pl-3 pr-3">
                        <div className="font-medium">{s.title}</div>
                        <div className="text-xs text-muted-foreground">
                          {MODE_TITLES[s.mode] ?? s.mode} · {SESSION_STATUS_TITLES[s.status] ?? s.status}
                        </div>
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap">{s.started_at ? formatDateTime(s.started_at) : "—"}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">
                        {s.attempts}
                        {s.evaluated > 0 && <span className="text-xs text-muted-foreground"> · зачтено {s.passed}</span>}
                        {s.critical_failures.length > 0 && (
                          <div className="text-xs text-destructive" data-testid="critical-failures">
                            не зачтено: критическая ошибка — {s.critical_failures.join("; ")}
                          </div>
                        )}
                      </td>
                      <td className={cn("py-2 pr-3 text-right font-medium tabular-nums", low && "text-destructive")}>{formatScore(s.average)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{formatDuration(s.average_seconds)}</td>
                      <td className={cn("py-2 pr-3 text-right tabular-nums", (s.average_deviation ?? 0) > 0 && "text-destructive")}>{formatDeviation(s.average_deviation)}</td>
                      <td className="py-2 pr-3">
                        <div className="flex flex-wrap gap-1">
                          {s.comments > 0 && <Badge tone="primary">{s.comments} {plural(s.comments, "комментарий", "комментария", "комментариев")}</Badge>}
                          {s.overridden > 0 && <Badge tone="warning">оценка изменена</Badge>}
                        </div>
                      </td>
                    </tr>
                    {(s.stream || s.advice.length > 0) && (
                      <tr className="border-b last:border-0">
                        <td colSpan={7} className="space-y-2 pb-3 pl-3 pr-3">
                          {s.stream && <StreamSummaryView summary={s.stream} />}
                          <AdviceList title="Советы по занятию" points={s.advice} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Частые ошибки</CardTitle>
          </CardHeader>
          <CardContent className="text-sm" data-testid="progress-errors">
            {p.frequent_errors.length === 0 ? (
              <p className="text-muted-foreground">Повторяющихся ошибок нет.</p>
            ) : (
              <ul className="space-y-2">
                {p.frequent_errors.map((e) => (
                  <li key={e.code} className="flex items-start justify-between gap-3 rounded-md border p-2">
                    <div>
                      <div className="font-medium">{e.title}</div>
                      {e.memo_ref && (
                        <Link to={`/student/reference?q=${encodeURIComponent(e.title)}`} className="inline-flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline">
                          <BookOpen className="size-3" aria-hidden /> Памятка, {e.memo_ref}
                        </Link>
                      )}
                    </div>
                    <span className="shrink-0 text-muted-foreground">× {e.count}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <TrendingUp className="size-4" aria-hidden /> Рекомендации
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            {p.recommendations.length === 0 ? (
              <p className="text-muted-foreground">Пройдите первое занятие — рекомендации появятся по его итогам.</p>
            ) : (
              <ul className="list-disc space-y-1 pl-5" data-testid="recommendations">
                {p.recommendations.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <Ratings p={p} />
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

/** How a rating reads for the trainee: the numbers (start 1400) mean nothing to him. */
function ratingLevel(rating: number): { label: string; tone: "success" | "warning" | "danger" } {
  if (rating > START_RATING + RATING_BAND) return { label: "хорошо", tone: "success" };
  if (rating < START_RATING - RATING_BAND) return { label: "надо подтянуть", tone: "danger" };
  return { label: "средне", tone: "warning" };
}

function Ratings({ p }: { p: ProgressOut }) {
  if (p.ratings.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Рейтинг по группам происшествий</CardTitle>
        <p className="text-sm text-muted-foreground">По оценённым карточкам. Сверху — что подтянуть.</p>
      </CardHeader>
      <CardContent data-testid="ratings-chart">
        <ul className="divide-y">
          {p.ratings.map((r) => {
            const level = ratingLevel(r.rating);
            return (
              <li key={`${r.mode}:${r.incident_group}`} className="flex items-start justify-between gap-3 py-2 text-sm">
                <span>
                  {r.title}
                  {r.mode === "call_intake" && <span className="text-muted-foreground"> (приём вызова)</span>}
                  <span className="block text-xs text-muted-foreground">
                    по {r.n} {plural(r.n, "попытке", "попыткам", "попыткам")}
                  </span>
                </span>
                <Badge tone={level.tone} className="shrink-0">
                  {level.label}
                </Badge>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
