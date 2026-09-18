import { BarChart3 } from "lucide-react";
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useGroupAnalytics, type GroupAnalyticsOut, type ReadinessOut } from "@/api/analytics";
import { useGroups } from "@/api/teacher";
import { heatmapOption, scoreDynamicsOption, timeDynamicsOption } from "@/analytics/charts";
import { Chart } from "@/components/chart";
import { usePalette } from "@/components/chart-theme";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { formatDate } from "@/emulator/time";
import { cn } from "@/lib/utils";
import { MODE_TITLES, plural } from "@/teacher/labels";

const PERIODS = [7, 14, 28, 90];
// The pass threshold the heat map and the score line mark; sessions may set their own.
const THRESHOLD = 70;

const RISK_TITLES: Record<string, { title: string; tone: BadgeTone }> = {
  low: { title: "Готов", tone: "success" },
  medium: { title: "Под вопросом", tone: "warning" },
  high: { title: "Риск", tone: "danger" },
  unknown: { title: "Нет данных", tone: "neutral" },
};

/** «Аналитика группы» (PRD 13.7): heat map, dynamics, typical errors and the readiness
 *  forecast with an explanation; the group and the period are in the URL. */
export function TeacherAnalyticsPage() {
  const groups = useGroups();
  const [params, setParams] = useSearchParams();
  const groupId = params.get("group") ?? groups.data?.[0]?.id;
  const days = Number(params.get("days") ?? 28) || 28;

  function update(next: { group?: string; days?: number }) {
    const merged = new URLSearchParams(params);
    if (next.group) merged.set("group", next.group);
    if (next.days) merged.set("days", String(next.days));
    setParams(merged, { replace: true });
  }

  if (groups.isPending) return <LoadingState text="Загружаем группы…" />;
  if (groups.isError) return <ErrorState message={groups.error.message} onRetry={() => void groups.refetch()} />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Аналитика группы</h1>
          <p className="text-sm text-muted-foreground">Слабые места, динамика и прогноз готовности к аттестации по оценённым попыткам.</p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="group">Группа</Label>
            <select id="group" className="h-9 rounded-md border bg-background px-2 text-sm" value={groupId ?? ""} onChange={(e) => update({ group: e.target.value })}>
              {groups.data.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.title}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="days">Период</Label>
            <select id="days" className="h-9 rounded-md border bg-background px-2 text-sm" value={days} onChange={(e) => update({ days: Number(e.target.value) })}>
              {PERIODS.map((d) => (
                <option key={d} value={d}>
                  {d} {plural(d, "день", "дня", "дней")}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>
      {groupId ? (
        <GroupAnalytics groupId={groupId} days={days} />
      ) : (
        <Card>
          <CardContent className="py-10 text-center text-muted-foreground">
            Групп пока нет. Создайте группу в разделе «Группы» — аналитика появится после первого занятия.
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function GroupAnalytics({ groupId, days }: { groupId: string; days: number }) {
  const query = useGroupAnalytics(groupId, days);
  if (query.isPending) return <LoadingState text="Считаем аналитику…" />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const data = query.data;
  const empty = data.volume.attempts === 0;
  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground" data-testid="analytics-volume">
        {data.group_title}: {data.volume.attempts} {plural(data.volume.attempts, "оценённая попытка", "оценённые попытки", "оценённых попыток")} у{" "}
        {data.volume.students_with_attempts} из {data.volume.students} обучающихся, {data.volume.sessions} {plural(data.volume.sessions, "занятие", "занятия", "занятий")} · с{" "}
        {formatDate(data.period.since)} по {formatDate(data.period.until)}
        {data.demo_data && (
          <Badge tone="warning" className="ml-2">
            демонстрационные данные
          </Badge>
        )}
      </p>

      <Card>
        <CardHeader>
          <CardTitle>Выжимка</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm leading-relaxed" data-testid="analytics-summary">
            {data.summary}
          </p>
        </CardContent>
      </Card>

      {empty ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
            <BarChart3 className="size-8" aria-hidden />
            <p>За выбранный период оценённых попыток нет.</p>
            <p className="text-xs">Проведите занятие или выберите период длиннее — графики построятся по закрытым карточкам.</p>
          </CardContent>
        </Card>
      ) : (
        <>
          <Heatmaps data={data} />
          <Dynamics data={data} />
          <Errors data={data} />
        </>
      )}

      <Readiness data={data} />
    </div>
  );
}

function Heatmaps({ data }: { data: GroupAnalyticsOut }) {
  const p = usePalette();
  const modes = useMemo(() => Array.from(new Set(data.heatmap.map((c) => c.mode))).sort(), [data.heatmap]);
  const options = useMemo(
    () => modes.map((mode) => ({ mode, option: heatmapOption(p, data.heatmap, data.students, data.incident_groups, mode, THRESHOLD) })),
    [p, modes, data],
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle>Тепловая карта: обучающиеся × группы происшествий</CardTitle>
        <p className="text-sm text-muted-foreground">
          Средний балл по оценённым попыткам за период; ▾ — ниже {THRESHOLD}. Пустая клетка — таких карточек ещё не было.
        </p>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-2">
        {options.map(({ mode, option }) => (
          <div key={mode} data-testid={`heatmap-${mode}`}>
            <h3 className="mb-1 text-sm font-medium">{MODE_TITLES[mode] ?? mode}</h3>
            <Chart option={option} height={Math.max(220, 40 * data.students.length + 90)} label={`Тепловая карта: ${MODE_TITLES[mode] ?? mode}`} />
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function Dynamics({ data }: { data: GroupAnalyticsOut }) {
  const p = usePalette();
  const score = useMemo(() => scoreDynamicsOption(p, data.dynamics, THRESHOLD), [p, data.dynamics]);
  const time = useMemo(() => timeDynamicsOption(p, data.dynamics), [p, data.dynamics]);
  const caption = `Период ${formatDate(data.period.since)} — ${formatDate(data.period.until)} · ${data.volume.attempts} попыток`;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Динамика по неделям</CardTitle>
        <p className="text-sm text-muted-foreground">{caption}</p>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-2">
        <div data-testid="dynamics-score">
          <Chart option={score} height={260} label="Средний балл по неделям" />
        </div>
        <div data-testid="dynamics-time">
          <Chart option={time} height={260} label="Время относительно норматива по неделям" />
        </div>
      </CardContent>
    </Card>
  );
}

function Errors({ data }: { data: GroupAnalyticsOut }) {
  if (data.errors.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Типичные ошибки группы</CardTitle>
        <p className="text-sm text-muted-foreground">Пять самых частых за период: сколько раз и у какой доли обучающихся.</p>
      </CardHeader>
      <CardContent>
        <ol className="space-y-2 text-sm" data-testid="top-errors">
          {data.errors.map((e, i) => (
            <li key={e.code} className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="w-5 text-right text-muted-foreground">{i + 1}.</span>
              <span className="font-medium">{e.title}</span>
              <span className="text-muted-foreground">
                × {e.count} · у {e.students} из {data.volume.students} ({Math.round(e.students_share * 100)} %)
              </span>
              <span className="h-2 flex-1 basis-32 overflow-hidden rounded bg-muted">
                <span className="block h-full bg-destructive/70" style={{ width: `${Math.round(e.students_share * 100)}%` }} />
              </span>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function Readiness({ data }: { data: GroupAnalyticsOut }) {
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2 space-y-0">
        <div>
          <CardTitle>Прогноз готовности к аттестации</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">Вероятность сдать аттестацию по всей истории обучающегося и две главные причины.</p>
        </div>
        <Link to="/teacher/analytics/readiness-model" className="text-sm text-primary hover:underline">
          Достоверность прогноза →
        </Link>
      </CardHeader>
      <CardContent>
        {!data.model_available && (
          <p className="mb-3 text-sm text-warning-foreground">Модель прогноза не обучена: запустите обучение на стенде, см. «Достоверность прогноза».</p>
        )}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm" aria-label="Прогноз готовности" data-testid="readiness-table">
            <thead className="text-left text-xs text-muted-foreground">
              <tr className="border-b">
                <th className="py-2 pr-3 font-medium">Обучающийся</th>
                <th className="py-2 pr-3 text-right font-medium">Попыток</th>
                <th className="py-2 pr-3 text-right font-medium">Вероятность</th>
                <th className="py-2 pr-3 font-medium">Риск</th>
                <th className="py-2 pr-3 font-medium">Почему</th>
              </tr>
            </thead>
            <tbody>
              {data.readiness.map((r) => (
                <ReadinessRow key={r.student_id} row={r} />
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function ReadinessRow({ row }: { row: ReadinessOut }) {
  const risk = RISK_TITLES[row.risk] ?? { title: "Нет данных", tone: "neutral" as BadgeTone };
  const pct = row.probability === null ? null : Math.round(row.probability * 100);
  return (
    <tr className={cn("border-b last:border-0", row.risk === "high" && "bg-destructive/5")} data-risk={row.risk}>
      <td className="py-2 pr-3 font-medium">{row.full_name}</td>
      <td className="py-2 pr-3 text-right tabular-nums">{row.attempts}</td>
      <td className="py-2 pr-3 text-right tabular-nums">
        {pct === null ? (
          "—"
        ) : (
          <span className="inline-flex items-center gap-2">
            <span className="h-2 w-16 overflow-hidden rounded bg-muted">
              <span className={cn("block h-full", row.risk === "low" ? "bg-success" : row.risk === "medium" ? "bg-warning" : "bg-destructive")} style={{ width: `${pct}%` }} />
            </span>
            {pct} %
          </span>
        )}
      </td>
      <td className="py-2 pr-3">
        <Badge tone={risk.tone}>{risk.title}</Badge>
      </td>
      <td className="py-2 pr-3 text-muted-foreground">
        <ul className="list-disc pl-4">
          {row.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </td>
    </tr>
  );
}
