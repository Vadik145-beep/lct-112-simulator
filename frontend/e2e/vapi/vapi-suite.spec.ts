import { appendFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import {
  BASE, CONFIGURED, OUT, ROOT, STUDENT, STUDENT_PASSWORD, TEACHER, TEACHER_PASSWORD,
  answerCall, apiToken, collect, converse, fillCard, hangupAndSave, loginWithForm, prepareLesson,
  type AttemptData, type CardVariant, type LessonOptions,
} from "./lib/flow";
import { checkEvaluation, type Check } from "./lib/scoring";

// Голосовые тесты с облачным заявителем Vapi (docs/VOICE_TESTS.md): каждый тест — отдельный
// живой звонок на стенде с включённым облаком, свой режим занятия / поведение диспетчера /
// вариант карточки. После звонка оценка сверяется с независимым пересчётом (lib/scoring.ts)
// плюс ожидания конкретного случая. Сводка прогона: out/suite-<метка>.md (строка на тест).
//
// Запуск (около 40 минут, расходует кредиты Vapi):
//   E2E_BASE_URL=https://<стенд> E2E_TEACHER_LOGIN=… E2E_TEACHER_PASSWORD=… \
//   E2E_STUDENT_LOGIN=… E2E_STUDENT_PASSWORD=… npx playwright test e2e/vapi
// Без этих переменных набор пропускается (обычный `npm run e2e` его не трогает).
// E2E_CASES=04,05 — выборочно; E2E_HEADLESS=1 — без окна.

// One label per run: the hour is enough to keep reruns of the same session in one file.
const STAMP = process.env.SUITE_STAMP ?? new Date().toISOString().slice(0, 13).replace(/[:T]/g, "-");
const SUMMARY = resolve(OUT, `suite-${STAMP}.md`);
const FULL = ["01-what-happened", "02-address", "03-entrance", "04-injured", "05-danger", "06-name", "07-phone", "08-closing"];

interface Case {
  id: string;
  title: string;
  lesson?: Omit<LessonOptions, "title">;
  script: string[];
  card: CardVariant;
  /** Ждать, пока заявитель сам положит трубку (мс), прежде чем завершать самим. */
  waitCallerHangup?: number;
  expect: (d: AttemptData, shown: number, log: string[]) => Check[];
}

const c = (name: string, expected: unknown, actual: unknown, ok: boolean): Check => ({ name, expected, actual, ok });
const item = <T>(d: AttemptData, key: string, i = 0) => (d.evaluation.components[key]?.items[i] ?? {}) as T;
const comp = (d: AttemptData, key: string) => d.evaluation.components[key]!;

const CASES: Case[] = [
  {
    id: "01-full",
    title: "полный опрос по регламенту, карточка по эталону → 100",
    script: FULL,
    card: "reference",
    expect: (d) => [
      c("итог = 100", 100, d.evaluation.total, d.evaluation.total === 100),
      c("темы 7/7", 7, item<{ covered: string[] }>(d, "required_topics").covered.length, item<{ covered: string[] }>(d, "required_topics").covered.length === 7),
    ],
  },
  {
    id: "02-no-name-phone",
    title: "не спросили имя и телефон → обязательные вопросы 5/7",
    script: ["01-what-happened", "02-address", "03-entrance", "04-injured", "05-danger", "08-closing"],
    card: "reference",
    expect: (d) => {
      const rt = item<{ missing: string[] }>(d, "required_topics");
      return [
        c("телефон не выяснен", true, rt.missing, rt.missing.includes("callback_phone")),
        c("обязательные вопросы < max", `< ${comp(d, "required_topics").max}`, comp(d, "required_topics").score, comp(d, "required_topics").score < comp(d, "required_topics").max),
      ];
    },
  },
  {
    id: "03-wrong-type-house",
    title: "тип — только группа, дом 83 вместо 81 → опросная карта 20 %, адрес без дома",
    script: FULL,
    card: "wrong-type-house",
    expect: (d) => {
      const sc = comp(d, "survey_card");
      const house = (comp(d, "address").items as { part: string; match: number }[]).find((p) => p.part === "house");
      return [
        c("опросная карта = 20 % max", Math.round(sc.max * 0.2 * 10) / 10, sc.score, Math.abs(sc.score - sc.max * 0.2) < 0.06),
        c("дом не совпал", 0, house?.match, house?.match === 0),
        c("адрес < max", `< ${comp(d, "address").max}`, comp(d, "address").score, comp(d, "address").score < comp(d, "address").max),
      ];
    },
  },
  {
    id: "04-norm-100",
    title: "норматив 100 с при разговоре дольше → время по линейному спаду",
    lesson: { norm_seconds: 100 },
    script: FULL,
    card: "reference",
    expect: (d) => {
      const t = item<{ seconds: number; norm_seconds: number }>(d, "time");
      return [
        c("норматив занятия попал в оценку", 100, t.norm_seconds, t.norm_seconds === 100),
        c("разговор дольше норматива", "> 100", t.seconds, t.seconds > 100),
        c("время < max", `< ${comp(d, "time").max}`, comp(d, "time").score, comp(d, "time").score < comp(d, "time").max),
      ];
    },
  },
  {
    id: "05-custom-weights",
    title: "свои веса (карта 40, время 30, сумма 135) → нормировка к 100 с остатком",
    lesson: { weights: { survey_card: 40, time: 30 } },
    script: FULL,
    card: "reference",
    expect: (d) => [
      c("max опросной карты = 32 (29 + остаток 3)", 32, comp(d, "survey_card").max, comp(d, "survey_card").max === 32),
      c("max времени = 22", 22, comp(d, "time").max, comp(d, "time").max === 22),
      c("max адреса = 11", 11, comp(d, "address").max, comp(d, "address").max === 11),
    ],
  },
  {
    id: "06-empty-card",
    title: "полный разговор, карточка пустая («пропуск») → незачёт",
    script: FULL,
    card: "empty",
    expect: (d) => [
      c("опросная карта = 0", 0, comp(d, "survey_card").score, comp(d, "survey_card").score === 0),
      c("адрес = 0", 0, comp(d, "address").score, comp(d, "address").score === 0),
      c("итог < 70", "< 70", d.evaluation.total, d.evaluation.total < 70),
      c("незачёт", false, d.evaluation.passed, d.evaluation.passed === false),
    ],
  },
  {
    id: "07-typos",
    title: "описание с ошибками («газавой», «требуеться») → грамотность −2 за замечание; тип «кухня» → карта 60 %",
    script: FULL,
    card: "typos",
    expect: (d) => {
      const g = comp(d, "grammar");
      const sc = comp(d, "survey_card");
      return [
        c("опросная карта = 60 % max (неверен нижний уровень)", Math.round(sc.max * 0.6 * 10) / 10, sc.score, Math.abs(sc.score - sc.max * 0.6) < 0.06),
        c("грамотность проверена", "checked", g.status, g.status === "checked"),
        c("замечаний ≥ 1", "≥ 1", g.items.length, g.items.length >= 1),
        c("грамотность = max − 2·N", Math.max(0, g.max - 2 * g.items.length), g.score, Math.abs(g.score - Math.max(0, g.max - 2 * g.items.length)) < 0.06),
      ];
    },
  },
  {
    id: "08-silent-dispatcher",
    title: "диспетчер ничего не спрашивает («алло», «оставайтесь на линии») → темы только со слов заявителя",
    script: ["10-hello", "11-wait", "08-closing"],
    card: "reference",
    expect: (d) => {
      const rt = item<{ covered: string[]; missing: string[] }>(d, "required_topics");
      return [
        c("не выяснено ≥ 3 тем", "≥ 3", rt.missing.length, rt.missing.length >= 3),
        c("обязательные вопросы < max", `< ${comp(d, "required_topics").max}`, comp(d, "required_topics").score, comp(d, "required_topics").score < comp(d, "required_topics").max),
      ];
    },
  },
  {
    id: "09-threshold-99",
    title: "порог зачёта 99 при неполном опросе → высокий балл, но незачёт",
    lesson: { pass_threshold: 99 },
    script: ["01-what-happened", "02-address", "03-entrance", "04-injured", "05-danger", "08-closing"],
    card: "reference",
    expect: (d) => [
      c("порог занятия = 99", 99, d.session.pass_threshold, d.session.pass_threshold === 99),
      c("итог ≥ 80", "≥ 80", d.evaluation.total, d.evaluation.total >= 80),
      c("незачёт из-за порога", false, d.evaluation.passed, d.evaluation.passed === false),
    ],
  },
  {
    id: "10-reordered-repeat",
    title: "другой порядок вопросов + «повторите адрес» → темы не зависят от порядка",
    script: ["06-name", "07-phone", "02-address", "09-repeat", "03-entrance", "01-what-happened", "04-injured", "05-danger", "08-closing"],
    card: "reference",
    expect: (d) => {
      // «repeat»/«unknown» движок в темы хода не пишет (SERVICE_TOPICS) — проверяем по тексту.
      const repeat = d.turns.some((t) => t.role === "operator" && /повтор/i.test(t.text));
      const addressReplies = d.turns.filter((t) => t.role === "caller" && t.topics.includes("address")).length;
      return [
        c("просьба повторить услышана (в стенограмме)", true, repeat, repeat),
        c("заявитель назвал адрес ≥ 2 раз", "≥ 2", addressReplies, addressReplies >= 2),
        c("темы 7/7", 7, item<{ covered: string[] }>(d, "required_topics").covered.length, item<{ covered: string[] }>(d, "required_topics").covered.length === 7),
      ];
    },
  },
  {
    id: "11-early-goodbye",
    title: "прощание после двух вопросов → заявитель сам кладёт трубку, оценка по частичному опросу",
    script: ["01-what-happened", "02-address", "08-closing"],
    card: "reference",
    waitCallerHangup: 30_000,
    expect: (d, _shown, log) => {
      const callerHungUp = log.some((l) => /уже завершён/.test(l));
      const ended = log.some((l) => /завершил звонок|уже завершён/.test(l));
      return [
        c(`звонок завершён (${callerHungUp ? "заявитель положил трубку сам" : "диспетчер завершил"})`, true, ended, ended),
        c("обязательные вопросы < max", `< ${comp(d, "required_topics").max}`, comp(d, "required_topics").score, comp(d, "required_topics").score < comp(d, "required_topics").max),
      ];
    },
  },
  {
    id: "12-flags-extra-service",
    title: "лишний признак «Пострадавшие» → признаки 2/3, лишние службы по правилам признака, Жаккар < 1",
    script: FULL,
    card: "flags-extra",
    expect: (d) => {
      const flags = item<{ flags_wrong: string[]; flags_fraction: number }>(d, "flags_services", 0);
      const services = item<{ extra: string[]; jaccard: number }>(d, "flags_services", 1);
      return [
        c("неверный признак injured", ["injured"], flags.flags_wrong, flags.flags_wrong.length === 1 && flags.flags_wrong[0] === "injured"),
        c("доля верных признаков 0.67", 0.67, flags.flags_fraction, Math.abs(flags.flags_fraction - 0.67) < 0.02),
        c("лишние службы от признака (≥ 1)", "≥ 1", services.extra, services.extra.length >= 1),
        c("Жаккар < 1", "< 1", services.jaccard, services.jaccard < 1),
        c("признаки и службы < max", `< ${comp(d, "flags_services").max}`, comp(d, "flags_services").score, comp(d, "flags_services").score < comp(d, "flags_services").max),
      ];
    },
  },
];

test.use({
  headless: process.env.E2E_HEADLESS === "1",
  launchOptions: { args: ["--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"] },
  permissions: ["microphone"],
  viewport: { width: 1280, height: 800 },
  video: "off",
});

// Sequential (fullyParallel: false) but not serial: one failed call does not cancel the rest.
// Vapi/WebRTC may fail to connect once in a while, so each call gets a second try.
test.describe.configure({ retries: 1 });
test.skip(!CONFIGURED, "нужны E2E_TEACHER_LOGIN/PASSWORD и E2E_STUDENT_LOGIN/PASSWORD стенда с облачным голосом");

function row(cols: unknown[]): string {
  return `| ${cols.map((x) => String(x).replace(/\|/g, "\\|")).join(" | ")} |\n`;
}

test.beforeAll(() => {
  mkdirSync(OUT, { recursive: true });
  if (!existsSync(SUMMARY)) {
    writeFileSync(
      SUMMARY,
      `# Голосовые тесты Vapi · ${STAMP}\n\nСтенд: ${BASE}\n\n` +
        row(["№", "Тест", "Итог", "Зачёт", "Ходы облака", "Темы", "Проверок", "Расхождения", "Время, с"]) +
        row(["---", "---", "---", "---", "---", "---", "---", "---", "---"]),
    );
  }
});

const only = process.env.E2E_CASES?.split(",").map((s) => s.trim()).filter(Boolean);

for (const kase of CASES) {
  if (only && !only.some((o) => kase.id.startsWith(o))) continue;
  test(`${kase.id}: ${kase.title}`, async ({ page, request, context }) => {
    test.setTimeout(12 * 60_000);
    const startedAt = Date.now();
    await context.addInitScript({ path: resolve(ROOT, "lib/audio-bridge.js") });
    const teacher = await apiToken(request, TEACHER, TEACHER_PASSWORD);
    const lesson = await prepareLesson(request, teacher, { title: `Vapi ${kase.id} · ${STAMP}`, ...kase.lesson });

    await loginWithForm(page, STUDENT, STUDENT_PASSWORD);
    const attemptId = await answerCall(page, lesson.sessionId);
    const log = await converse(page, kase.script);
    await fillCard(page, kase.card);
    const shown = await hangupAndSave(page, log, kase.waitCallerHangup ?? 0);
    await page.screenshot({ path: resolve(OUT, `${STAMP}-${kase.id}${test.info().retry ? `-retry${test.info().retry}` : ""}.png`), fullPage: true });

    const student = await apiToken(request, STUDENT, STUDENT_PASSWORD);
    const data = await collect(request, teacher, student, lesson.sessionId, attemptId);
    await request.post(`/api/sessions/${lesson.sessionId}/finish`, { headers: teacher });

    const cloudTurns = data.turns.filter((t) => (t as { method?: string }).method === "cloud").length;
    const checks: Check[] = [
      c("балл на карточке = итог API", data.evaluation.total, shown, shown === data.evaluation.total),
      c("режим диалога cloud", "cloud", data.dialogMode, data.dialogMode === "cloud"),
      // A call that dropped right after joining would otherwise pass on the card alone.
      c("разговор с облаком состоялся (ходов из облака ≥ 2)", "≥ 2", cloudTurns, cloudTurns >= 2),
      ...checkEvaluation(data.evaluation, {
        weights: data.session.weights ?? lesson.weights,
        turns: data.turns,
        topicsTable: data.topicsTable,
        required: data.required,
        passThreshold: data.session.pass_threshold,
      }),
      ...kase.expect(data, shown, log),
    ];
    const failed = checks.filter((x) => !x.ok);
    const rt = (data.evaluation.components.required_topics?.items[0] ?? { covered: [] }) as { covered: string[] };
    const seconds = Math.round((Date.now() - startedAt) / 1000);
    appendFileSync(
      SUMMARY,
      row([
        kase.id, kase.title, data.evaluation.total, data.evaluation.passed ? "да" : "нет", cloudTurns,
        `${rt.covered.length}/${data.required.length}`, checks.length,
        failed.length ? failed.map((f) => `${f.name}: ждали ${JSON.stringify(f.expected)}, получили ${JSON.stringify(f.actual)}`).join("; ") : "—",
        seconds,
      ]),
    );
    writeFileSync(
      resolve(OUT, `${STAMP}-${kase.id}${test.info().retry ? `-retry${test.info().retry}` : ""}.json`),
      JSON.stringify({ case: kase.id, title: kase.title, sessionId: lesson.sessionId, attemptId, shown, log, checks, evaluation: data.evaluation, turns: data.turns }, null, 2),
    );
    expect(failed, failed.map((f) => `${f.name}: ждали ${JSON.stringify(f.expected)}, получили ${JSON.stringify(f.actual)}`).join("\n")).toEqual([]);
  });
}
