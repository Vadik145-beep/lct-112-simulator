import type { HeatCellOut, WeekPointOut } from "@/api/analytics";
import { baseOption, type ChartOption, type ChartPalette } from "@/components/chart-theme";
import { formatDate } from "@/emulator/time";
import { MODE_TITLES } from "@/teacher/labels";

export interface Person {
  id: string;
  full_name: string;
}

export interface IncidentGroup {
  code: string;
  title: string;
}

/** Surname and initials for axis labels. */
export function shortName(fullName: string): string {
  const [surname = "", ...rest] = fullName.split(" ");
  const initials = rest
    .filter(Boolean)
    .map((w) => `${w[0] ?? ""}.`)
    .join(" ");
  return initials ? `${surname} ${initials}` : surname;
}

export function shortTitle(title: string, max = 22): string {
  return title.length > max ? `${title.slice(0, max - 1)}…` : title;
}

/** Trainees × incident groups for one mode: mean score in the cell, count in the tooltip. */
export function heatmapOption(
  p: ChartPalette,
  cells: HeatCellOut[],
  students: Person[],
  groups: IncidentGroup[],
  mode: string,
  threshold: number,
): ChartOption {
  const rows = students.map((s) => shortName(s.full_name));
  const cols = groups.map((g) => shortTitle(g.title));
  const counts = new Map<string, number>();
  const data = cells
    .filter((c) => c.mode === mode)
    .map((c) => {
      const x = groups.findIndex((g) => g.code === c.incident_group);
      const y = students.findIndex((s) => s.id === c.student_id);
      counts.set(`${x}:${y}`, c.count);
      return [x, y, c.mean] as [number, number, number];
    })
    .filter(([x, y]) => x >= 0 && y >= 0);
  return {
    ...baseOption(p),
    grid: { left: 130, right: 24, top: 8, bottom: 70 },
    tooltip: {
      ...(baseOption(p).tooltip as object),
      formatter: (params: { value: number[] }) => {
        const [x = -1, y = -1, mean = 0] = params.value;
        const n = counts.get(`${x}:${y}`) ?? 0;
        return `${students[y]?.full_name ?? ""}<br/>${groups[x]?.title ?? ""}<br/>средний балл <b>${mean}</b> по ${n} попыт.`;
      },
    },
    xAxis: {
      type: "category",
      data: cols,
      name: "Группа происшествий",
      nameLocation: "middle",
      nameGap: 56,
      axisLabel: { color: p.muted, interval: 0, rotate: cols.length > 4 ? 20 : 0, fontSize: 11 },
      axisLine: { lineStyle: { color: p.border } },
      splitArea: { show: true },
    },
    yAxis: {
      type: "category",
      data: rows,
      name: "Обучающиеся",
      nameLocation: "end",
      axisLabel: { color: p.text, fontSize: 12 },
      axisLine: { lineStyle: { color: p.border } },
      splitArea: { show: true },
    },
    visualMap: {
      min: 0,
      max: 100,
      show: false,
      inRange: { color: [p.danger, p.warning, p.success] },
    },
    series: [
      {
        name: MODE_TITLES[mode] ?? mode,
        type: "heatmap",
        data,
        label: {
          show: true,
          color: p.text,
          formatter: (params: { value: number[] }) => {
            const mean = params.value[2] ?? 0;
            return `${Math.round(mean)}${mean < threshold ? " ▾" : ""}`;
          },
        },
        itemStyle: { borderColor: p.card, borderWidth: 2 },
        emphasis: { itemStyle: { shadowBlur: 6, shadowColor: "rgba(0,0,0,0.3)" } },
      },
    ],
  };
}

/** Mean score by week, one line per mode; the threshold as a dashed guide. */
export function scoreDynamicsOption(p: ChartPalette, points: WeekPointOut[], threshold: number): ChartOption {
  return dynamicsOption(p, points, {
    value: (w) => w.mean_score,
    yName: "Средний балл",
    yMin: 0,
    yMax: 100,
    guide: threshold,
    guideName: "Порог",
    format: (v) => `${v}`,
  });
}

/** Mean time as a share of the norm by week; 100 % is the norm. */
export function timeDynamicsOption(p: ChartPalette, points: WeekPointOut[]): ChartOption {
  return dynamicsOption(p, points, {
    value: (w) => Math.round(w.mean_time_ratio * 100),
    yName: "Время, % от норматива",
    yMin: 0,
    guide: 100,
    guideName: "Норматив",
    format: (v) => `${v} %`,
  });
}

function dynamicsOption(
  p: ChartPalette,
  points: WeekPointOut[],
  opts: { value: (w: WeekPointOut) => number; yName: string; yMin: number; yMax?: number; guide: number; guideName: string; format: (v: number) => string },
): ChartOption {
  const weeks = Array.from(new Set(points.map((w) => w.week_start))).sort();
  const labels = weeks.map((w) => `нед. с ${formatDate(w)}`);
  const modes = Array.from(new Set(points.map((w) => w.mode))).sort();
  const series = modes.map((mode, i) => ({
    name: MODE_TITLES[mode] ?? mode,
    type: "line",
    smooth: true,
    symbolSize: 8,
    connectNulls: true,
    lineStyle: { width: 2.5, color: p.series[i % p.series.length] },
    itemStyle: { color: p.series[i % p.series.length] },
    data: weeks.map((week) => {
      const point = points.find((w) => w.week_start === week && w.mode === mode);
      return point ? { value: opts.value(point), count: point.count } : null;
    }),
  }));
  const guide = {
    name: opts.guideName,
    type: "line",
    silent: true,
    showSymbol: false,
    lineStyle: { type: "dashed", width: 1, color: p.muted },
    itemStyle: { color: p.muted },
    data: weeks.map(() => opts.guide),
  };
  return {
    ...baseOption(p),
    grid: { left: 48, right: 16, top: 44, bottom: 40 },
    legend: { top: 0, right: 0, textStyle: { color: p.text } },
    tooltip: {
      ...(baseOption(p).tooltip as object),
      trigger: "axis",
      formatter: (items: { seriesName: string; data: { value: number; count: number } | null; axisValue: string }[]) => {
        const lines = items
          .filter((it) => it.data && typeof it.data === "object")
          .map((it) => `${it.seriesName}: <b>${opts.format(it.data!.value)}</b> (${it.data!.count} попыт.)`);
        return `${items[0]?.axisValue ?? ""}<br/>${lines.join("<br/>")}`;
      },
    },
    xAxis: {
      type: "category",
      data: labels,
      name: "Неделя",
      nameLocation: "middle",
      nameGap: 28,
      axisLabel: { color: p.muted, fontSize: 11 },
      axisLine: { lineStyle: { color: p.border } },
    },
    yAxis: {
      type: "value",
      name: opts.yName,
      nameTextStyle: { color: p.muted, align: "left" },
      min: opts.yMin,
      max: opts.yMax,
      axisLabel: { color: p.muted, formatter: (v: number) => opts.format(v) },
      splitLine: { lineStyle: { color: p.border } },
    },
    series: [...series, guide],
  };
}
