import { useMemo } from "react";
import { Link } from "react-router-dom";

import { useReadinessModel, type ReadinessModelOut } from "@/api/analytics";
import { Chart } from "@/components/chart";
import { baseOption, usePalette, type ChartOption, type ChartPalette } from "@/components/chart-theme";
import { ErrorState, LoadingState } from "@/components/states";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/emulator/time";

/** «Достоверность прогноза»: how the readiness model was trained and how far to trust it
 *  (PRD 9.7): AUC, Brier score, calibration and the weights of the features. */
export function ReadinessModelPage() {
  const query = useReadinessModel();
  if (query.isPending) return <LoadingState text="Читаем метрики модели…" />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const m = query.data;
  return (
    <div className="space-y-6">
      <div>
        <Link to="/teacher/analytics" className="text-sm text-muted-foreground hover:underline">
          ← К аналитике
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">Достоверность прогноза</h1>
        <p className="text-sm text-muted-foreground">Что стоит за колонкой «Вероятность» и насколько ей можно верить.</p>
      </div>

      {!m.trained ? (
        <Card>
          <CardContent className="py-10 text-center text-muted-foreground">
            Модель ещё не обучена. На стенде: <code>python scripts/simulate_cohort.py</code> и{" "}
            <code>python -m app.domain.analytics.train_readiness</code> — метрики появятся здесь.
          </CardContent>
        </Card>
      ) : (
        <Metrics m={m} />
      )}

      <Card>
        <CardHeader>
          <CardTitle>Как это работает, простыми словами</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm leading-relaxed">
          <p>
            Прогноз — это логистическая регрессия: по восьми признакам истории обучающегося (средний балл последних десяти попыток, тренд, время
            относительно норматива, неверные решения, пропущенные обязательные вопросы, типичные ошибки на попытку, рейтинг самой слабой группы,
            число попыток) она даёт вероятность, что обучающийся сдаст аттестацию. Две причины в таблице — признаки, которые сильнее всего
            сдвинули прогноз.
          </p>
          <p>
            <b>Откуда данные.</b> Реальных аттестаций у системы пока нет, поэтому модель обучена на виртуальной когорте из 300 обучающихся: у
            каждого скрытый уровень, своя скорость обучения и шум; история — от 5 до 60 попыток, «аттестация» — 20 отложенных попыток, которых
            модель не видит. Когорту можно пересоздать и переобучить модель на стенде; по мере накопления настоящих аттестаций её нужно
            переобучить на реальных данных.
          </p>
          <p>
            <b>Как читать метрики.</b> ROC AUC — насколько модель отличает готовых от неготовых: 0,5 — как монетка, 1,0 — идеально. Brier —
            средний квадрат ошибки вероятности: чем меньше, тем лучше; рядом Brier «без модели» (когда всем ставят одну и ту же долю).
            Калибровка — совпадает ли обещанная вероятность с фактической долей сдавших в каждой корзине.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function Metrics({ m }: { m: ReadinessModelOut }) {
  const p = usePalette();
  const calibration = useMemo(() => calibrationOption(p, m), [p, m]);
  const weights = useMemo(() => weightsOption(p, m), [p, m]);
  return (
    <>
      <section className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Метрики" data-testid="model-metrics">
        <Stat label="ROC AUC на отложенной выборке" value={fmt(m.roc_auc, 3)} hint={(m.roc_auc ?? 0) >= 0.75 ? "не ниже 0,75 — порог принят" : "ниже порога 0,75"} />
        <Stat label="Brier (меньше — лучше)" value={fmt(m.brier, 3)} hint={`без модели ${fmt(m.brier_baseline, 3)}`} />
        <Stat label="Обучение / проверка" value={`${m.n_train} / ${m.n_test}`} hint={`всего ${m.n_total} виртуальных обучающихся`} />
        <Stat label="Доля готовых в проверке" value={m.positive_share_test === null || m.positive_share_test === undefined ? "—" : `${Math.round(m.positive_share_test * 100)} %`} hint={m.trained_at ? `обучено ${formatDateTime(m.trained_at)}` : ""} />
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Калибровка: обещанная вероятность и факт</CardTitle>
          <p className="text-sm text-muted-foreground">
            Отложенная выборка, {m.n_test} обучающихся, десять корзин по прогнозу. Столбики — сколько человек в корзине; точки — доля сдавших;
            пунктир — идеал (прогноз равен факту). Малые корзины (1–3 человека) шумят, это нормально.
          </p>
        </CardHeader>
        <CardContent data-testid="calibration-chart">
          <Chart option={calibration} height={300} label="Калибровочный график" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Вес признаков</CardTitle>
          <p className="text-sm text-muted-foreground">Коэффициенты на стандартизованных признаках: вправо — повышает вероятность, влево — понижает.</p>
        </CardHeader>
        <CardContent data-testid="weights-chart">
          <Chart option={weights} height={40 * m.features.length + 60} label="Веса признаков модели" />
        </CardContent>
      </Card>
    </>
  );
}

function fmt(value: number | null | undefined, digits: number): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits).replace(".", ",");
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border bg-card px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

function calibrationOption(p: ChartPalette, m: ReadinessModelOut): ChartOption {
  const buckets = m.calibration;
  const labels = buckets.map((b) => `${Math.round(b.lower * 100)}–${Math.round(b.upper * 100)} %`);
  return {
    ...baseOption(p),
    grid: { left: 56, right: 56, top: 60, bottom: 48 },
    legend: { top: 0, right: 0, textStyle: { color: p.text } },
    tooltip: {
      ...(baseOption(p).tooltip as object),
      trigger: "axis",
      formatter: (items: { axisValue: string; dataIndex: number }[]) => {
        const b = buckets[items[0]?.dataIndex ?? 0];
        if (!b || !b.count) return `${items[0]?.axisValue ?? ""}: пусто`;
        return `Прогноз ${items[0]?.axisValue ?? ""}<br/>средний прогноз <b>${Math.round((b.predicted ?? 0) * 100)} %</b><br/>сдали фактически <b>${Math.round((b.observed ?? 0) * 100)} %</b><br/>${b.count} чел.`;
      },
    },
    xAxis: {
      type: "category",
      data: labels,
      name: "Обещанная вероятность",
      nameLocation: "middle",
      nameGap: 32,
      axisLabel: { color: p.muted, fontSize: 11 },
      axisLine: { lineStyle: { color: p.border } },
    },
    yAxis: [
      {
        type: "value",
        min: 0,
        max: 100,
        name: "Доля сдавших, %",
        nameTextStyle: { color: p.muted, align: "left" },
        axisLabel: { color: p.muted },
        splitLine: { lineStyle: { color: p.border } },
      },
      {
        type: "value",
        min: 0,
        name: "Человек",
        nameTextStyle: { color: p.muted, align: "right" },
        axisLabel: { color: p.muted },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "Человек в корзине",
        type: "bar",
        yAxisIndex: 1,
        data: buckets.map((b) => b.count),
        itemStyle: { color: p.border, borderRadius: 3 },
        barMaxWidth: 36,
      },
      {
        name: "Идеал",
        type: "line",
        silent: true,
        showSymbol: false,
        lineStyle: { type: "dashed", color: p.muted, width: 1 },
        itemStyle: { color: p.muted },
        data: buckets.map((b) => Math.round(((b.lower + b.upper) / 2) * 100)),
      },
      {
        name: "Сдали фактически",
        type: "line",
        symbolSize: 9,
        connectNulls: false,
        lineStyle: { color: p.primary, width: 2.5 },
        itemStyle: { color: p.primary },
        data: buckets.map((b) => (b.observed === null ? null : Math.round(b.observed * 100))),
      },
    ],
  };
}

function weightsOption(p: ChartPalette, m: ReadinessModelOut): ChartOption {
  const features = [...m.features].sort((a, b) => a.weight - b.weight);
  return {
    ...baseOption(p),
    grid: { left: 240, right: 24, top: 8, bottom: 32 },
    tooltip: { ...(baseOption(p).tooltip as object), trigger: "axis" },
    xAxis: {
      type: "value",
      name: "Вес",
      nameLocation: "middle",
      nameGap: 24,
      axisLabel: { color: p.muted },
      splitLine: { lineStyle: { color: p.border } },
    },
    yAxis: {
      type: "category",
      data: features.map((f) => f.title),
      axisLabel: { color: p.text, fontSize: 12 },
      axisLine: { lineStyle: { color: p.border } },
    },
    series: [
      {
        name: "Вес",
        type: "bar",
        data: features.map((f) => ({ value: f.weight, itemStyle: { color: f.weight >= 0 ? p.success : p.danger, borderRadius: 3 } })),
        barMaxWidth: 22,
        label: { show: true, position: "right", color: p.text, formatter: (x: { value: number }) => x.value.toFixed(2) },
      },
    ],
  };
}
