import { BookOpen, TrendingUp } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import { scoreDynamicsOption, shortTitle, timeDynamicsOption } from "@/analytics/charts";
import { useProgress, type ProgressOut } from "@/api/review";
import { Chart } from "@/components/chart";
import { baseOption, usePalette, type ChartOption, type ChartPalette } from "@/components/chart-theme";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDate, formatDateTime } from "@/emulator/time";
import { cn } from "@/lib/utils";
import { MODE_TITLES, SESSION_STATUS_TITLES, formatDeviation, formatDuration, formatScore, plural } from "@/teacher/labels";

// The pass threshold the score line marks; the start of every skill rating (PRD 9.7).
const THRESHOLD = 70;
const START_RATING = 1400;

/** «Мой прогресс» (PRD 13.7): the score per lesson, time against the norm, frequent errors
 * and what to read, plus the skill rating by incident group and the weekly dynamics. */
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
          Баллы по занятиям, время против норматива, рейтинг по группам происшествий, частые ошибки и что повторить.
          {p.demo_data && (
            <Badge tone="warning" className="ml-2">
              демонстрационные данные
            </Badge>
          )}
        </p>
      </div>

      <section aria-label="Итоги" className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="progress-stats">
        <Stat label="Карточек и вызовов" value={String(p.attempts)} />
        <Stat label="Оценено" value={String(p.evaluated)} />
        <Stat label="Зачтено" value={p.evaluated ? `${p.passed} (${Math.round((100 * p.passed) / p.evaluated)} %)` : "—"} />
        <Stat label="Средний балл" value={formatScore(p.average)} />
      </section>

      <Ratings p={p} />
      <Dynamics p={p} />

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
                  <tr key={s.session_id} className="border-b last:border-0">
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

function Ratings({ p }: { p: ProgressOut }) {
  const palette = usePalette();
  const option = useMemo(() => ratingsOption(palette, p), [palette, p]);
  if (p.ratings.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Рейтинг по группам происшествий</CardTitle>
        <p className="text-sm text-muted-foreground">
          Начало — {START_RATING}; растёт за хорошие результаты (сильнее — на сложных карточках), падает за слабые. Слабые группы сверху.
        </p>
      </CardHeader>
      <CardContent data-testid="ratings-chart">
        <Chart option={option} height={Math.max(160, 30 * p.ratings.length + 60)} label="Рейтинг по группам происшествий" />
      </CardContent>
    </Card>
  );
}

function ratingsOption(palette: ChartPalette, p: ProgressOut): ChartOption {
  // Bars grow from the start rating: right is above 1400, left is below.
  const rows = p.ratings;
  const spread = Math.max(100, ...rows.map((r) => Math.abs(r.rating - START_RATING)));
  const limit = Math.ceil(spread / 50) * 50;
  return {
    ...baseOption(palette),
    grid: { left: 250, right: 48, top: 8, bottom: 32 },
    tooltip: {
      ...(baseOption(palette).tooltip as object),
      trigger: "axis",
      formatter: (items: { dataIndex: number }[]) => {
        const r = rows[items[0]?.dataIndex ?? 0];
        if (!r) return "";
        return `${r.title} · ${MODE_TITLES[r.mode] ?? r.mode}<br/>рейтинг <b>${Math.round(r.rating)}</b> по ${r.n} ${plural(r.n, "попытке", "попыткам", "попыткам")}`;
      },
    },
    xAxis: {
      type: "value",
      min: -limit,
      max: limit,
      name: "Рейтинг (старт 1400)",
      nameLocation: "middle",
      nameGap: 24,
      axisLabel: { color: palette.muted, formatter: (v: number) => String(START_RATING + v) },
      splitLine: { lineStyle: { color: palette.border } },
    },
    yAxis: {
      type: "category",
      // The API sorts from the weakest; the category axis draws its first item at the bottom.
      inverse: true,
      data: rows.map((r) => `${shortTitle(r.title, 26)} · ${r.mode === "call_intake" ? "вызов" : "карточка"}`),
      axisLabel: { color: palette.text, fontSize: 12 },
      axisLine: { lineStyle: { color: palette.muted } },
    },
    series: [
      {
        name: "Рейтинг",
        type: "bar",
        barMaxWidth: 18,
        data: rows.map((r) => ({
          value: Math.round(r.rating - START_RATING),
          itemStyle: { color: r.rating < START_RATING - 25 ? palette.danger : r.rating > START_RATING + 25 ? palette.success : palette.warning, borderRadius: 3 },
        })),
        label: {
          show: true,
          position: "right",
          color: palette.text,
          formatter: (x: { value: number }) => String(START_RATING + x.value),
        },
      },
    ],
  };
}

function Dynamics({ p }: { p: ProgressOut }) {
  const palette = usePalette();
  const score = useMemo(() => scoreDynamicsOption(palette, p.dynamics, THRESHOLD), [palette, p.dynamics]);
  const time = useMemo(() => timeDynamicsOption(palette, p.dynamics), [palette, p.dynamics]);
  if (p.dynamics.length === 0) return null;
  const first = p.dynamics[0]?.week_start;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Динамика по неделям</CardTitle>
        <p className="text-sm text-muted-foreground">
          С {formatDate(first)} · {p.evaluated} оценённых попыток
        </p>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-2">
        <Chart option={score} height={240} label="Мой средний балл по неделям" />
        <Chart option={time} height={240} label="Моё время относительно норматива по неделям" />
      </CardContent>
    </Card>
  );
}
