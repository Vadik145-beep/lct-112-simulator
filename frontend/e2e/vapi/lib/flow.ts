import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, type APIRequestContext, type Page } from "@playwright/test";

import type { CallerTopic, DialogTurn, Evaluation } from "./scoring";

// Общие шаги для голосовых тестов с Vapi (docs/VOICE_TESTS.md): занятие по API, вход,
// звонок, реплики диспетчера через синтетический микрофон, варианты заполнения карточки,
// сохранение и выгрузка оценки. Учётные записи только из окружения (см. README в docs).

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const OUT = resolve(ROOT, "out");
export const PHRASES = resolve(ROOT, "phrases");
export const BASE = process.env.E2E_BASE_URL ?? "https://localhost";
export const TEACHER = process.env.E2E_TEACHER_LOGIN ?? "";
export const TEACHER_PASSWORD = process.env.E2E_TEACHER_PASSWORD ?? "";
export const STUDENT = process.env.E2E_STUDENT_LOGIN ?? "";
export const STUDENT_PASSWORD = process.env.E2E_STUDENT_PASSWORD ?? "";
/** The suite runs only when all four accounts variables are set (a stand with the cloud). */
export const CONFIGURED = Boolean(TEACHER && TEACHER_PASSWORD && STUDENT && STUDENT_PASSWORD);
export const SCENARIO_TITLE = "Свист от газовой трубы в квартире";
export const REPLY_TIMEOUT = 35_000;

export type Headers = Record<string, string>;

export interface LessonOptions {
  title: string;
  dialog_mode?: string;
  norm_seconds?: number;
  pass_threshold?: number;
  weights?: Record<string, number>;
  hints_enabled?: boolean;
}

export interface LessonInfo {
  sessionId: string;
  weights: Record<string, number>;
  pass_threshold: number;
  norm_seconds: number;
}

export async function apiToken(request: APIRequestContext, login: string, password: string): Promise<Headers> {
  const res = await request.post("/api/auth/login", { data: { login, password } });
  expect(res.ok(), `вход ${login} на ${BASE}: ${res.status()} ${await res.text()}`).toBeTruthy();
  return { Authorization: `Bearer ${((await res.json()) as { access_token: string }).access_token}` };
}

export async function loginWithForm(page: Page, login: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Логин").fill(login);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Мои задания" })).toBeVisible({ timeout: 20_000 });
}

/** Занятие «Приём вызова» на сценарии про газовую трубу; другие идущие занятия завершаются. */
export async function prepareLesson(request: APIRequestContext, teacher: Headers, opts: LessonOptions): Promise<LessonInfo> {
  const mode = opts.dialog_mode ?? "cloud";
  const models = (await (await request.get("/api/models", { headers: teacher })).json()) as { cloud?: boolean; stt: boolean };
  if (mode === "cloud") expect(models.cloud, `на ${BASE} облачный голос выключен (GET /api/models → cloud=false)`).toBe(true);

  const students = (await (await request.get("/api/students", { headers: teacher })).json()) as { id: string; login: string }[];
  const student = students.find((s) => s.login === STUDENT);
  expect(student, `обучающийся ${STUDENT} не найден у преподавателя ${TEACHER}`).toBeTruthy();
  const groups = (await (await request.get("/api/groups", { headers: teacher })).json()) as { id: string; members: { login: string }[] }[];
  let group = groups.find((g) => g.members.some((m) => m.login === STUDENT));
  if (!group) {
    const created = await request.post("/api/groups", { headers: teacher, data: { title: `Тест Vapi · ${Date.now()}`, student_ids: [student!.id] } });
    expect(created.status(), await created.text()).toBe(201);
    group = (await created.json()) as typeof group;
  }
  const list = (await (await request.get(`/api/scenarios?kind=call_intake&q=${encodeURIComponent("газовой трубы")}`, { headers: teacher })).json()) as {
    items: { id: string; title: string }[];
  };
  const scenario = list.items.find((s) => s.title === SCENARIO_TITLE);
  expect(scenario, `сценарий «${SCENARIO_TITLE}» не найден`).toBeTruthy();

  const sessions = (await (await request.get("/api/sessions", { headers: teacher })).json()) as { id: string; status: string; mode: string }[];
  for (const s of sessions.filter((x) => x.status === "running" && x.mode === "call_intake")) {
    await request.post(`/api/sessions/${s.id}/finish`, { headers: teacher });
  }

  const created = await request.post("/api/sessions", {
    headers: teacher,
    data: {
      title: opts.title,
      mode: "call_intake",
      group_id: group!.id,
      card_source: "scenarios",
      scenario_ids: [scenario!.id],
      norm_seconds: opts.norm_seconds ?? 180,
      pass_threshold: opts.pass_threshold ?? 70,
      cards_per_student: 1,
      hints_enabled: opts.hints_enabled ?? true,
      voice_enabled: true,
      dialog_mode: mode,
      weights: opts.weights ?? {},
    },
  });
  expect(created.status(), `создание занятия: ${await created.text()}`).toBe(201);
  const session = (await created.json()) as { id: string; weights: Record<string, number>; pass_threshold: number; norm_seconds: number };
  const started = await request.post(`/api/sessions/${session.id}/start`, { headers: teacher });
  expect(started.ok(), `старт занятия: ${await started.text()}`).toBeTruthy();
  return { sessionId: session.id, weights: session.weights ?? {}, pass_threshold: session.pass_threshold, norm_seconds: session.norm_seconds };
}

/** Открывает АРМ по занятию и принимает звонок; возвращает id попытки. */
export async function answerCall(page: Page, sessionId: string): Promise<string> {
  await page.goto(`/student/sessions/${sessionId}/calls`);
  await expect(page).toHaveURL(/\/student\/attempts\/[0-9a-f-]+$/, { timeout: 20_000 });
  const attemptId = page.url().split("/").pop()!;
  const panel = page.getByTestId("call-panel");
  await expect(panel).toContainText("входящий", { timeout: 20_000 });
  await page.mouse.click(640, 10);
  await page.getByTestId("call-answer").click();
  await expect(panel).toContainText("разговор", { timeout: 60_000 });
  await expect(page.getByTestId("cloud-live"), `облако не соединилось: ${await panel.textContent()}`).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("list", { name: "Стенограмма разговора" }).locator("li[data-role=caller]")).toHaveCount(1, { timeout: 30_000 });
  await page.waitForTimeout(6000);
  return attemptId;
}

export async function say(page: Page, file: string): Promise<number> {
  const b64 = readFileSync(resolve(PHRASES, `${file}.wav`)).toString("base64");
  const duration = (await page.evaluate(
    ([audio, label]) => (window as unknown as { __voice: { say: (a: string, l: string) => Promise<number> } }).__voice.say(audio, label),
    [b64, file],
  )) as number;
  await page.waitForTimeout(duration * 1000 + 500);
  return duration;
}

/** Реплики по порядку; после каждой ждёт ответ заявителя. Останавливается, если звонок кончился. */
export async function converse(page: Page, script: string[]): Promise<string[]> {
  const log: string[] = [];
  const callerLines = page.getByRole("list", { name: "Стенограмма разговора" }).locator("li[data-role=caller]");
  for (const file of script) {
    if (!(await page.getByTestId("cloud-live").isVisible())) {
      log.push(`звонок завершился до реплики ${file}`);
      break;
    }
    const before = await callerLines.count();
    await say(page, file);
    log.push(`диспетчер → ${file}`);
    if (file === "08-closing") break;
    try {
      await expect(callerLines).toHaveCount(before + 1, { timeout: REPLY_TIMEOUT });
      log.push(`заявитель ← ${((await callerLines.nth(before).textContent()) ?? "").trim().slice(0, 100)}`);
    } catch {
      log.push("заявитель ← (ответа не появилось)");
    }
    await page.waitForTimeout(2500);
  }
  return log;
}

export type CardVariant = "reference" | "wrong-type-house" | "empty" | "typos" | "flags-extra";

async function pickSign(page: Page, title: string) {
  await page.getByTestId("survey-card").getByRole("button", { name: title, exact: true }).first().click();
}

async function fillAddress(page: Page, house: string) {
  const street = page.getByRole("combobox", { name: "Улица" });
  await street.fill("Вавил");
  await page.getByRole("listbox", { name: "Подсказка улиц" }).getByRole("button", { name: /ЮЗАО, Ломоносовский/ }).click();
  await expect(street).toHaveValue("улица Вавилова");
  await page.getByLabel("Дом/Вл.").fill(house);
  await page.getByLabel("Корпус").fill("1");
  await page.getByLabel("Квартира/офис").fill("5");
  await page.getByLabel("Подъезд").fill("1");
  await page.getByLabel("Этаж").fill("2");
  await page.getByLabel("Код").fill("5В");
}

export async function fillCard(page: Page, variant: CardVariant) {
  if (variant === "empty") {
    await page.getByLabel("Описание со слов заявителя").fill("пропуск");
    return;
  }
  await page.getByLabel("Фамилия и имя заявителя").fill("Трунов Олег Егорович");
  await page.getByLabel("Статус заявителя").selectOption("жилец");
  await page.getByLabel("предоставленный").fill("916-320-12-83");
  await fillAddress(page, variant === "wrong-type-house" ? "83" : "81");
  await page.getByLabel("Описание со слов заявителя").fill(
    variant === "typos"
      ? "Свист от газавой трубы в квартире, на кухне. 03 не требуеться."
      : "Свист от газовой трубы в квартире, на кухне. 03 не требуется.",
  );
  await page.getByRole("button", { name: "добавить тип происшествия" }).click();
  await page.getByRole("listbox", { name: "Группа происшествия" }).getByRole("button", { name: /^13\./ }).click();
  if (variant === "wrong-type-house") {
    // Верна только группа 13: «на улице → коллектор» (13.1.1.0) вместо 13.2.4.0.
    await pickSign(page, "Запах газа на улице");
    await pickSign(page, "Коллектор");
  } else if (variant === "typos") {
    // Верный тип, неверен нижний уровень: «в помещении → кухня» (13.2.1.0) вместо 13.2.4.0.
    await pickSign(page, "Запах газа в помещении");
    await pickSign(page, "Кухня");
  } else {
    await pickSign(page, "Запах газа в помещении");
    await pickSign(page, "От газововго оборудования");
  }
  await expect(page.getByTestId("survey-type")).toContainText(/газ/i);
  await expect(page.getByTestId("services-strip").locator("[data-service='mosgaz']")).toBeVisible({ timeout: 15_000 });
  if (variant === "flags-extra") {
    // Признак «Пострадавшие» сам добавляет службы по правилам классификатора (ПСЦ, 103).
    await page.getByRole("button", { name: "Пострадавшие", exact: true }).click();
    await page.waitForTimeout(1500);
  }
}

/** Кладёт трубку (если заявитель ещё не положил) и сохраняет карточку; возвращает балл с карточки. */
export async function hangupAndSave(page: Page, log: string[], waitCallerHangup = 0): Promise<number> {
  const live = page.getByTestId("cloud-live");
  if (waitCallerHangup > 0) {
    await expect(live).toBeHidden({ timeout: waitCallerHangup }).catch(() => null);
  }
  const hangup = page.getByTestId("call-hangup");
  if (await hangup.isVisible()) {
    await hangup.click();
    log.push("диспетчер завершил звонок");
  } else {
    const text = ((await page.getByTestId("call-panel").textContent()) ?? "").replace(/\s+/g, " ").trim();
    log.push(`звонок уже завершён: ${text.slice(0, 120)}`);
  }
  await page.waitForTimeout(3000);
  await page.getByTestId("save-card").click();
  await expect(page.getByTestId("card-score")).toBeVisible({ timeout: 180_000 });
  return Number(await page.getByTestId("card-score").textContent());
}

export interface AttemptData {
  evaluation: Evaluation;
  required: string[];
  turns: DialogTurn[];
  dialogMode: string;
  topicsTable: CallerTopic[];
  session: { pass_threshold: number; weights: Record<string, number>; norm_seconds: number };
}

export async function collect(request: APIRequestContext, teacher: Headers, student: Headers, sessionId: string, attemptId: string): Promise<AttemptData> {
  const attempt = (await (await request.get(`/api/attempts/${attemptId}`, { headers: student })).json()) as {
    evaluation: Evaluation | null;
    intake: { required_topics: string[] };
  };
  expect(attempt.evaluation, "оценка не посчитана").toBeTruthy();
  const dialog = (await (await request.get(`/api/attempts/${attemptId}/dialog`, { headers: student })).json()) as { turns: DialogTurn[]; mode: string };
  const topicsTable = (await (await request.get("/api/caller-topics", { headers: student })).json()) as CallerTopic[];
  const session = (await (await request.get(`/api/sessions/${sessionId}`, { headers: teacher })).json()) as AttemptData["session"];
  return { evaluation: attempt.evaluation!, required: attempt.intake.required_topics, turns: dialog.turns, dialogMode: dialog.mode, topicsTable, session };
}
