import { expect, test, type Page } from "@playwright/test";

/**
 * Ручной прогон режима ДДС по живому стенду: преподаватель заводит занятие, обучающийся
 * работает с карточкой, принимает доклады бригады и закрывает карточку. Снимки экрана
 * складываются в ../docs/screenshots/manual-dds.
 *
 * Запуск: E2E_BASE_URL=https://155-212-186-2.sslip.io npx playwright test e2e/manual-dds.spec.ts
 */

const PASSWORD = process.env.PROBE_PASSWORD ?? "Probe12345";
const SHOTS = "../docs/screenshots/manual-dds";
const stamp = new Date().toLocaleTimeString("ru-RU");

async function signIn(page: Page, login: string) {
  await page.goto("/login");
  const field = page.getByLabel("Логин");
  await expect(field).toBeVisible();
  await field.fill(login);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  // Вошли по-настоящему: появился кабинет обучающегося, а не форма входа.
  await expect(page.getByRole("heading", { name: "Мои задания" })).toBeVisible({
    timeout: 30_000,
  });
}

async function setStatus(
  page: Page,
  status: string,
  opts: { orderNumber?: string; comment?: string } = {},
) {
  await page.getByRole("button", { name: "Проставить статус" }).click();
  const form = page.getByRole("form", { name: "Проставление статуса" });
  await form.getByLabel("Статус", { exact: true }).selectOption({ label: status });
  if (opts.orderNumber) await form.getByLabel("Номер наряда").fill(opts.orderNumber);
  if (opts.comment)
    await form.getByLabel("Комментарий", { exact: true }).fill(opts.comment);
  await form.getByRole("button", { name: "Сохранить статус" }).click();
  await expect(form).toBeHidden();
}

test("диспетчер ДДС принимает доклады бригады", async ({ page, request }) => {
  test.setTimeout(600_000);

  // --- преподаватель: занятие на карточке «Нет отопления» с быстрой бригадой
  const token = await (async () => {
    const r = await request.post("/api/auth/login", {
      data: { login: "probe_teacher", password: PASSWORD },
    });
    expect(r.ok(), await r.text()).toBeTruthy();
    return ((await r.json()) as { access_token: string }).access_token;
  })();
  const th = { Authorization: `Bearer ${token}` };

  const found = (await (
    await request.get("/api/scenarios", {
      headers: th,
      params: { kind: "card_response", q: "Нет отопления" },
    })
  ).json()) as { items: { id: string; title: string }[] };
  expect(found.items.length, "в библиотеке есть карточка «Нет отопления»").toBeGreaterThan(0);
  const source = (await (
    await request.get(`/api/scenarios/${found.items[0].id}`, { headers: th })
  ).json()) as { body: Record<string, unknown> };

  // копия с короткими паузами, чтобы доклады пришли за время прогона
  const reference = source.body.reference as {
    reports: { status: string; text: string; after_seconds: number }[];
  };
  const body = {
    ...source.body,
    title: `Ручной прогон ДДС · ${stamp}`,
    ticket_ref: null,
    reference: {
      ...reference,
      reports: reference.reports.map((r) => ({ ...r, after_seconds: 5 })),
    },
  };
  const created = await request.post("/api/scenarios", {
    headers: th,
    data: { body, status: "review" },
  });
  expect(created.status(), await created.text()).toBe(201);
  const scenarioId = ((await created.json()) as { id: string }).id;
  const approved = await request.post(`/api/scenarios/${scenarioId}/approve`, {
    headers: th,
    data: { reference: true, replies: true, confirm_grammar: true },
  });
  expect(approved.status(), await approved.text()).toBe(200);

  const students = (await (await request.get("/api/students", { headers: th })).json()) as {
    id: string;
    login: string;
  }[];
  const student = students.find((s) => s.login === "probe_student") ?? students[0];
  const group = await request.post("/api/groups", {
    headers: th,
    data: { title: `Ручной прогон · ${stamp}`, student_ids: [student.id] },
  });
  expect(group.status(), await group.text()).toBe(201);
  const session = await request.post("/api/sessions", {
    headers: th,
    data: {
      title: `Ручной прогон ДДС · ${stamp}`,
      group_id: ((await group.json()) as { id: string }).id,
      difficulty: 1,
      scenario_ids: [scenarioId],
      service_profile: ["moek"],
      norm_seconds: 30,
      pass_threshold: 70,
    },
  });
  expect(session.status(), await session.text()).toBe(201);
  const sessionId = ((await session.json()) as { id: string }).id;
  expect((await request.post(`/api/sessions/${sessionId}/start`, { headers: th })).ok()).toBeTruthy();

  // --- обучающийся: открывает карточку
  // Считаем, что запись «прозвучала», если браузер скачал mp3 и запустил проигрывание.
  const media: { url: string; status: number; bytes: number }[] = [];
  page.on("response", (r) => {
    if (r.url().includes("/api/media/")) {
      media.push({
        url: r.url(),
        status: r.status(),
        bytes: Number(r.headers()["content-length"] ?? 0),
      });
    }
  });
  await page.addInitScript(() => {
    const played: string[] = [];
    (window as unknown as { __played: string[] }).__played = played;
    const play = HTMLAudioElement.prototype.play;
    HTMLAudioElement.prototype.play = function (this: HTMLAudioElement) {
      played.push(this.src || "(blob)");
      return play.call(this);
    };
  });
  await signIn(page, student.login);
  await page.goto(`/student/sessions/${sessionId}/journal`);
  await page.getByRole("link", { name: /Открыть карточку/ }).first().click();
  await expect(page.getByTestId("own-service-panel")).toContainText("Получена службой");
  await page.screenshot({ path: `${SHOTS}/01-карточка.png`, fullPage: true });

  // «Принята» с комментарием — так же, как это делает человек в живом АРМ
  await setStatus(page, "Принята", {
    comment: "Наряд направлен, бригада МОЭК выезжает",
  });
  await page.screenshot({ path: `${SHOTS}/02-принята.png`, fullPage: true });

  // --- доклад бригады: звонок ждёт ответа
  const report = page.locator('[data-testid="service-call-panel"][data-kind="report"]');
  await expect(report).toBeVisible({ timeout: 60_000 });
  await expect(report).toHaveAttribute("data-state", "ringing");
  await expect(report).toContainText("Звонит старший группы");
  await page.screenshot({ path: `${SHOTS}/03-входящий-доклад.png`, fullPage: true });

  await report.getByTestId("answer-report").click();
  await expect(report).toHaveAttribute("data-state", "talking");
  await expect(report).toContainText("Алло");
  await expect(report).toContainText("старший группы");
  await page.screenshot({ path: `${SHOTS}/04-доклад-принят.png`, fullPage: true });

  // звук: файл скачан и проигрывание запущено
  await expect
    .poll(async () => media.filter((m) => m.url.includes("tts/seed")).length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  const clip = media.find((m) => m.url.includes("tts/seed"));
  console.log("скачан файл озвучки:", clip?.url, clip?.status, clip?.bytes, "байт");
  expect(clip?.status).toBe(200);
  expect(clip?.bytes ?? 0).toBeGreaterThan(20_000);
  const played = await page.evaluate(
    () => (window as unknown as { __played: string[] }).__played,
  );
  console.log("запусков проигрывания:", played.length);
  expect(played.length).toBeGreaterThan(0);

  const said = (await report.locator("li[data-role='officer']").first().innerText()).trim();
  console.log("доклад 1:", said);
  expect(said).toContain("Выехали");

  // завершаем звонок и отражаем доклад статусом
  await report.getByRole("button", { name: "Завершить звонок" }).click();
  await expect(report).toHaveCount(0, { timeout: 30_000 });
  await setStatus(page, "Начало реагирования", {
    orderNumber: "МОЭК-317",
    comment: "Бригада выехала по докладу старшего группы",
  });
  await page.screenshot({ path: `${SHOTS}/05-начало-реагирования.png`, fullPage: true });

  // остальные три доклада тем же порядком
  for (const [phrase, status, comment] of [
    ["Прибыли", "Прибытие", "Бригада на месте"],
    ["Докладываю", "Проведение работ", "Осмотр ЦТП, остановка сетевого насоса"],
    ["Работы завершены", "Работы завершены", "Насос запущен, циркуляция восстановлена"],
  ] as const) {
    await expect(report).toBeVisible({ timeout: 90_000 });
    await expect(report).toHaveAttribute("data-state", "ringing");
    await report.getByTestId("answer-report").click();
    await expect(report).toContainText(phrase, { timeout: 30_000 });
    console.log(
      `доклад «${status}»:`,
      (await report.locator("li[data-role='officer']").first().innerText()).trim(),
    );
    await report.getByRole("button", { name: "Завершить звонок" }).click();
    await expect(report).toHaveCount(0, { timeout: 30_000 });
    await setStatus(page, status, { comment });
  }

  // карточка закрылась статусом «Работы завершены» — смотрим оценку и разбор
  await expect(page.getByTestId("card-score")).toHaveText(/^\d+$/, { timeout: 120_000 });
  const score = await page.getByTestId("card-score").innerText();
  console.log("балл за карточку:", score);
  await page.screenshot({ path: `${SHOTS}/06-оценка.png`, fullPage: true });

  await page.getByRole("link", { name: "Открыть разбор" }).click();
  await expect(page.getByText("Типичные ошибки", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Доклад бригады не принят/)).toHaveCount(0);
  await expect(page.getByText(/до доклада бригады/)).toHaveCount(0);
  await page.screenshot({ path: `${SHOTS}/07-разбор.png`, fullPage: true });
});
