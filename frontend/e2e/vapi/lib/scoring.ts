// Независимый пересчёт оценки режима «Приём вызова» по правилам движка
// (backend/app/domain/evaluation/{result,call_intake,timing,text}.py).
// Тест сверяет то, что показал тренажёр, с этим пересчётом: если движок ошибётся
// в нормировке весов, в итоге или в одной из составляющих — сверка это покажет.

export interface Component {
  key: string;
  title: string;
  score: number;
  max: number;
  status: "checked" | "not_checked" | "disabled";
  items: Record<string, unknown>[];
}

export interface Evaluation {
  mode: string;
  total: number;
  passed: boolean;
  components: Record<string, Component>;
  errors: { code: string; title: string; penalty: number; critical: boolean }[];
  methods: Record<string, string>;
}

export interface DialogTurn {
  role: "operator" | "caller";
  text: string;
  topics: string[];
}

export interface CallerTopic {
  code: string;
  keywords: string[];
}

export interface Check {
  name: string;
  expected: unknown;
  actual: unknown;
  ok: boolean;
}

export const DEFAULT_WEIGHTS: Record<string, number> = {
  survey_card: 25,
  flags_services: 10,
  address: 15,
  required_topics: 15,
  description: 10,
  time: 10,
  typical_errors: 5,
  grammar: 10,
};

/** normalize_text: нижний регистр, ё→е, пунктуация в пробелы, пробелы схлопнуты. */
export function normalizeText(text: string | null | undefined): string {
  if (!text) return "";
  return text
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/[^\p{L}\p{N}_\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** detect_topics: тема засчитана, если фраза содержит одно из её ключевых слов (по началу слова). */
export function detectTopics(text: string, table: CallerTopic[]): string[] {
  const normalized = normalizeText(text);
  if (!normalized) return [];
  const padded = ` ${normalized} `;
  const found: string[] = [];
  for (const topic of table) {
    if (!topic.keywords.length) continue;
    for (const raw of topic.keywords) {
      const wholeWord = raw.endsWith(" ");
      const keyword = normalizeText(raw);
      const needle = wholeWord ? ` ${keyword} ` : ` ${keyword}`;
      if (padded.includes(needle)) {
        found.push(topic.code);
        break;
      }
    }
  }
  return found;
}

/** normalize_weights: масштаб к сумме 100, остаток округления — самой тяжёлой составляющей. */
export function normalizeWeights(weights: Record<string, number>): Record<string, number> {
  const total = Object.values(weights).reduce((a, b) => a + b, 0);
  if (total <= 0) throw new Error("сумма весов должна быть больше нуля");
  if (total === 100) return { ...weights };
  const scaled: Record<string, number> = {};
  for (const [k, v] of Object.entries(weights)) scaled[k] = Math.floor((v * 100) / total);
  const remainder = 100 - Object.values(scaled).reduce((a, b) => a + b, 0);
  if (remainder) {
    let heaviest = Object.keys(weights)[0]!;
    for (const k of Object.keys(weights)) if (weights[k]! > weights[heaviest]!) heaviest = k;
    scaled[heaviest] += remainder;
  }
  return scaled;
}

export function timeFraction(seconds: number | null, norm: number): number {
  if (seconds === null) return 0;
  if (norm <= 0) return seconds <= 0 ? 1 : 0;
  if (seconds <= norm) return 1;
  if (seconds >= 2 * norm) return 0;
  return 1 - (seconds - norm) / norm;
}

/** scale: доля от максимума, округление до десятых (как round(x, 1) в Python). */
export function scale(max: number, fraction: number): number {
  const value = Math.max(0, Math.min(1, fraction)) * max;
  return Math.round(value * 10) / 10;
}

/** finalize: итог 0..100 по учитываемым составляющим, округление как в Python (banker's). */
export function total(components: Component[]): number {
  const counted = components.filter((c) => c.status === "checked");
  const maxSum = counted.reduce((a, c) => a + c.max, 0);
  if (maxSum <= 0) return 0;
  const raw = (counted.reduce((a, c) => a + c.score, 0) / maxSum) * 100;
  return Math.max(0, Math.min(100, roundHalfEven(raw)));
}

function roundHalfEven(x: number): number {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (Math.abs(diff - 0.5) < 1e-9) return floor % 2 === 0 ? floor : floor + 1;
  return Math.round(x);
}

function near(a: number, b: number, eps = 0.051): boolean {
  return Math.abs(a - b) <= eps;
}

/**
 * Сверка оценки попытки с независимым пересчётом. ``weights`` — веса занятия (пустые =
 * веса по умолчанию), ``turns`` — стенограмма из GET /attempts/{id}/dialog,
 * ``topicsTable`` — GET /api/caller-topics, ``required`` — обязательные темы сценария.
 */
export function checkEvaluation(
  evaluation: Evaluation,
  opts: {
    weights: Record<string, number>;
    turns: DialogTurn[];
    topicsTable: CallerTopic[];
    required: string[];
    passThreshold: number;
  },
): Check[] {
  const checks: Check[] = [];
  const push = (name: string, expected: unknown, actual: unknown, ok?: boolean) =>
    checks.push({ name, expected, actual, ok: ok ?? JSON.stringify(expected) === JSON.stringify(actual) });

  const components = Object.values(evaluation.components);

  // 1. Максимумы = нормированные веса занятия (все 8 составляющих приёма вызова применимы).
  const expectedMax = normalizeWeights({ ...DEFAULT_WEIGHTS, ...opts.weights });
  for (const key of Object.keys(DEFAULT_WEIGHTS)) {
    const c = evaluation.components[key];
    push(`max ${key}`, expectedMax[key], c?.max, c !== undefined && c.max === expectedMax[key]);
  }
  const maxSum = components.reduce((a, c) => a + c.max, 0);
  push("сумма максимумов = 100", 100, maxSum, near(maxSum, 100));

  // 2. Каждая составляющая в пределах [0, max]; выключенная — max 0.
  for (const c of components) {
    push(`0 ≤ ${c.key} ≤ max`, `0..${c.max}`, c.score, c.score >= 0 && c.score <= c.max + 1e-9);
    if (c.max === 0) push(`${c.key} disabled`, "disabled", c.status, c.status === "disabled");
  }

  // 3. Итог = сумма учитываемых / сумма их максимумов × 100 (не проверенные исключены).
  const expectedTotal = total(components);
  push("итог", expectedTotal, evaluation.total, expectedTotal === evaluation.total);

  // 4. Зачёт: итог ≥ порога и нет критических ошибок.
  const expectedPassed = evaluation.total >= opts.passThreshold && !evaluation.errors.some((e) => e.critical);
  push("зачёт", expectedPassed, evaluation.passed);

  // 5. Обязательные вопросы: темы = метки ходов (или ключевые слова по тексту).
  const topics = new Set<string>();
  for (const t of opts.turns) {
    const labels = t.topics && t.topics.length ? t.topics : detectTopics(t.text, opts.topicsTable);
    labels.forEach((l) => topics.add(l));
  }
  const covered = opts.required.filter((t) => topics.has(t));
  const rt = evaluation.components.required_topics;
  if (rt) {
    const fraction = opts.required.length ? covered.length / opts.required.length : 1;
    push("обязательные вопросы: балл", scale(rt.max, fraction), rt.score, near(scale(rt.max, fraction), rt.score));
    const item = rt.items[0] as { covered?: string[]; missing?: string[] } | undefined;
    push("обязательные вопросы: раскрытые", covered, item?.covered ?? []);
  }

  // 6. Время: доля от норматива по секундам, которые движок сам записал в items.
  const time = evaluation.components.time;
  if (time) {
    const item = time.items[0] as { seconds: number | null; norm_seconds: number };
    const expected = scale(time.max, timeFraction(item.seconds, item.norm_seconds));
    push(`время (${item.seconds} с при норме ${item.norm_seconds})`, expected, time.score, near(expected, time.score));
  }

  // 7. Типичные ошибки: max − сумма штрафов, не ниже 0.
  const te = evaluation.components.typical_errors;
  if (te) {
    const penalty = evaluation.errors.reduce((a, e) => a + e.penalty, 0);
    const expected = Math.max(0, te.max - penalty);
    push(`типичные ошибки (штраф ${penalty})`, expected, te.score, near(expected, te.score));
  }

  // 8. Грамотность: max − 2 × число замечаний, либо «не проверено».
  const gr = evaluation.components.grammar;
  if (gr) {
    if (gr.status === "not_checked") {
      push("грамотность не проверена → метод", "not_checked", evaluation.methods.grammar);
    } else {
      const expected = Math.max(0, gr.max - 2 * gr.items.length);
      push(`грамотность (${gr.items.length} замеч.)`, expected, gr.score, near(expected, gr.score));
    }
  }

  // 9. Признаки и службы: половина за признаки, половина по Жаккару из items.
  const fs = evaluation.components.flags_services;
  if (fs) {
    const flags = fs.items[0] as { flags_fraction: number };
    const services = fs.items[1] as { jaccard: number; expected_services: string[]; actual_services: string[] };
    const half = fs.max / 2;
    const inter = services.expected_services.filter((s) => services.actual_services.includes(s)).length;
    const union = new Set([...services.expected_services, ...services.actual_services]).size;
    const jaccard = union ? inter / union : 1;
    push("службы: Жаккар", Math.round(jaccard * 100) / 100, services.jaccard, near(Math.round(jaccard * 100) / 100, services.jaccard, 0.011));
    const expected = Math.round((scale(half, flags.flags_fraction) + scale(half, jaccard)) * 10) / 10;
    push("признаки и службы: балл", expected, fs.score, near(expected, fs.score, 0.11));
  }

  // 10. Адрес: взвешенные части из items (улица 3, дом 2, остальное по 1).
  const addr = evaluation.components.address;
  if (addr && addr.items.length) {
    const parts = addr.items as { weight: number; match: number }[];
    const w = parts.reduce((a, p) => a + p.weight, 0);
    const fraction = parts.reduce((a, p) => a + p.weight * p.match, 0) / w;
    push("адрес: балл", scale(addr.max, fraction), addr.score, near(scale(addr.max, fraction), addr.score));
  }

  // 11. Опросная карта: по совпавшим уровням из items.
  const sc = evaluation.components.survey_card;
  if (sc && sc.items.length) {
    const item = sc.items[0] as { matched_levels: number; levels: number };
    let fraction = 0;
    if (item.levels === 0 || item.matched_levels === item.levels) fraction = 1;
    else if (item.matched_levels === item.levels - 1 && item.matched_levels >= 2) fraction = 0.6;
    else if (item.matched_levels >= 1) fraction = 0.2;
    push("опросная карта: балл", scale(sc.max, fraction), sc.score, near(scale(sc.max, fraction), sc.score));
  }

  // 12. Описание: ключевые слова (половина) + близость (половина, по порогам из items).
  const d = evaluation.components.description;
  if (d && d.items.length >= 2) {
    const kw = d.items[0] as { keywords_found: string[]; keywords_missing: string[] };
    const sim = d.items[1] as { similarity: number; thresholds: [number, number] };
    const half = d.max / 2;
    const totalKw = kw.keywords_found.length + kw.keywords_missing.length;
    const kwFraction = totalKw ? kw.keywords_found.length / totalKw : 1;
    const simFraction = sim.similarity >= sim.thresholds[1] ? 1 : sim.similarity >= sim.thresholds[0] ? 0.5 : 0;
    const expected = Math.round((scale(half, kwFraction) + scale(half, simFraction)) * 10) / 10;
    push("описание: балл", expected, d.score, near(expected, d.score, 0.11));
  }

  return checks;
}
