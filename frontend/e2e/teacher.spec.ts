import { expect, test, type Browser, type Page } from "@playwright/test";

// Wave 4 acceptance ("Проверка" of plan/wave-04.md) against the compose stand: the teacher
// creates and starts a lesson, the trainee gets cards at once, the teacher sees every action
// live, finishes the lesson and reads the report. Screenshots go to docs/screenshots/wave-04/.

const SHOTS = "../docs/screenshots/wave-04";
const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";
// PRD 12 / plan wave 4: an action reaches the other side in under 2 seconds.
const LIVE_MS = 2000;

test.describe.configure({ mode: "serial" });

function watchNetwork(page: Page): { problems: string[]; foreign: string[] } {
  const problems: string[] = [];
  const foreign: string[] = [];
  const base = new URL(process.env.E2E_BASE_URL ?? "https://localhost");
  page.on("console", (msg) => {
    if (msg.type() === "error") problems.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (err) => problems.push(`pageerror: ${err.message}`));
  page.on("request", (req) => {
    if (new URL(req.url()).host !== base.host) foreign.push(req.url());
  });
  return { problems, foreign };
}

async function loginWithForm(page: Page, login: string) {
  await page.goto("/login");
  await page.getByLabel("Логин").fill(login);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
}

async function newPage(browser: Browser, viewport?: { width: number; height: number }) {
  const context = await browser.newContext(viewport ? { viewport } : {});
  return context.newPage();
}

/** Opens the status editor with the pencil and fills the row. */
async function setStatus(page: Page, status: string, opts: { orderNumber?: string; comment?: string; reason?: string } = {}) {
  await page.getByRole("button", { name: "Проставить статус" }).click();
  const form = page.getByRole("form", { name: "Проставление статуса" });
  await form.getByLabel("Статус", { exact: true }).selectOption({ label: status });
  if (opts.reason) await form.getByLabel("Причина отказа").selectOption({ label: opts.reason });
  if (opts.orderNumber) await form.getByLabel("Номер наряда").fill(opts.orderNumber);
  if (opts.comment) await form.getByLabel("Комментарий", { exact: true }).fill(opts.comment);
  await form.getByRole("button", { name: "Сохранить статус" }).click();
  // A final status waits for the evaluation (LanguageTool may take a few seconds when cold).
  await expect(form).toBeHidden({ timeout: 15_000 });
}

test.describe("Волна 4: занятие целиком", () => {
  test("преподаватель создаёт и запускает занятие, видит обучающегося вживую, завершает и читает отчёт", async ({ browser, request }) => {
    test.setTimeout(240_000);
    const teacher = await newPage(browser);
    const student = await newPage(browser);
    const teacherNet = watchNetwork(teacher);
    const studentNet = watchNetwork(student);
    const stamp = new Date().toLocaleTimeString("ru-RU");
    const title = `Проверка волны 4 · ${stamp}`;

    // Own group without student1: the wave 3 suite drives student1's demo lesson in parallel,
    // and a newer running lesson would otherwise take over its «Мои задания».
    const groupTitle = `Группа волны 4 · ${stamp}`;
    const login = await request.post("/api/auth/login", { data: { login: "teacher1", password: PASSWORD } });
    const headers = { Authorization: `Bearer ${((await login.json()) as { access_token: string }).access_token}` };
    const students = (await (await request.get("/api/students", { headers })).json()) as { id: string; login: string }[];
    const created = await request.post("/api/groups", {
      headers,
      data: { title: groupTitle, student_ids: students.filter((s) => ["student2", "student3"].includes(s.login)).map((s) => s.id) },
    });
    expect(created.status()).toBe(201);

    // --- teacher: list, create ------------------------------------------------------------
    await teacher.goto("/login");
    await teacher.getByRole("button", { name: "Войти как преподаватель" }).click();
    await expect(teacher.getByRole("heading", { name: "Занятия" })).toBeVisible();
    await teacher.screenshot({ path: `${SHOTS}/01-sessions.png`, fullPage: true });

    await teacher.getByRole("link", { name: "Создать занятие" }).click();
    const form = teacher.getByRole("form", { name: "Новое занятие" });
    await expect(form).toBeVisible();
    await form.getByLabel("Название").fill(title);
    const groupSelect = form.getByLabel("Группа");
    const groupValue = await groupSelect.locator("option", { hasText: groupTitle }).getAttribute("value");
    await groupSelect.selectOption(groupValue!);
    await form.getByLabel("Сложность").selectOption("3");
    await form.getByLabel("Территориальные ОИВ (управы, префектуры)").check();
    await form.getByLabel("Норматив, секунд").fill("30");
    await teacher.screenshot({ path: `${SHOTS}/02-session-form.png`, fullPage: true });
    await form.getByRole("button", { name: "Создать занятие" }).click();

    await expect(teacher.getByRole("heading", { name: title })).toBeVisible();
    await expect(teacher.getByText("Очередь карточек · 3")).toBeVisible();
    const sessionUrl = teacher.url();
    const sessionId = sessionUrl.split("/sessions/")[1]!;
    await teacher.screenshot({ path: `${SHOTS}/03-session-draft.png`, fullPage: true });

    // --- student: the lesson is listed, the journal waits for the start -------------------
    await loginWithForm(student, "student2");
    await expect(student.getByRole("heading", { name: "Мои задания" })).toBeVisible();
    const row = student.getByRole("listitem").filter({ hasText: title });
    await expect(row).toBeVisible();
    await row.getByRole("link", { name: "Ждать начала в журнале" }).click();
    await expect(student).toHaveURL(new RegExp(`/student/sessions/${sessionId}/journal$`));
    await expect(student.getByText("Занятие ещё не начато")).toBeVisible();
    await expect(student.getByRole("status").filter({ hasText: "связь есть" })).toBeVisible();

    // --- teacher starts, student gets three cards in under 2 s -----------------------------
    await teacher.getByRole("button", { name: "Начать занятие" }).click();
    await expect(teacher.getByText("Идёт", { exact: true })).toBeVisible();
    const startedAt = Date.now();
    await expect(student.locator("tr[data-attempt]")).toHaveCount(3, { timeout: LIVE_MS + 1000 });
    expect(Date.now() - startedAt).toBeLessThan(LIVE_MS + 1000);
    await expect(teacher.getByRole("list", { name: "Обучающиеся" }).getByRole("listitem")).toHaveCount(2);
    await teacher.screenshot({ path: `${SHOTS}/04-monitor-start.png`, fullPage: true });

    // --- student works, the tile follows in under 2 s --------------------------------------
    const tile = teacher.locator("[data-student=student2]");
    await expect(tile).toHaveAttribute("data-status", "waiting");
    await student.getByRole("link", { name: /Открыть карточку 38260311/ }).click();
    await expect(student.getByText("Происшествие 38260311")).toBeVisible();
    await student.getByRole("button", { name: "Проставить статус" }).click();
    await expect(tile.locator("[data-role=tile-status]")).toHaveText("проставляет статус", { timeout: LIVE_MS + 1000 });
    const statusForm = student.getByRole("form", { name: "Проставление статуса" });
    await statusForm.getByLabel("Статус", { exact: true }).selectOption({ label: "Принята" });
    const actedAt = Date.now();
    await statusForm.getByRole("button", { name: "Сохранить статус" }).click();
    await expect(tile).toHaveAttribute("data-status", "working", { timeout: LIVE_MS + 1000 });
    expect(Date.now() - actedAt).toBeLessThan(LIVE_MS + 1000);
    await expect(tile).toContainText("Принята");
    await teacher.screenshot({ path: `${SHOTS}/05-monitor-working.png`, fullPage: true });

    // The teacher opens the attempt in view mode.
    const monitorUrl = teacher.url();
    await tile.click();
    await expect(teacher.getByText("Просмотр · карточка 38260311")).toBeVisible();
    await expect(teacher.getByRole("list", { name: "Статусы" })).toContainText("Принята");
    await teacher.screenshot({ path: `${SHOTS}/06-attempt-view.png`, fullPage: true });
    await teacher.goto(monitorUrl);

    // Student closes the first card with the full chain, the tile shows the score.
    await setStatus(student, "Начало реагирования", { orderNumber: "14-217", comment: "Направлен дежурный слесарь, наряд 14-217" });
    await setStatus(student, "Прибытие");
    await setStatus(student, "Проведение работ", { comment: "Мусоропровод вскрыт, тлеющий мусор удалён" });
    await setStatus(student, "Работы завершены", { comment: "Задымление устранено, ствол промыт, пострадавших нет" });
    await expect(student.getByTestId("card-score")).toHaveText(/^\d+$/, { timeout: 15_000 });
    await expect(tile.locator("[data-role=last-score]")).toHaveText(/^\d+(\.\d)?$/, { timeout: LIVE_MS + 1000 });
    await expect(tile).toContainText("закрыто 1");

    // Second card: accepted only, left open; the third one is never touched.
    await student.goto(`/student/sessions/${sessionId}/journal`);
    await student.getByRole("link", { name: /Открыть карточку 38261102/ }).click();
    await setStatus(student, "Принята");

    // --- teacher finishes: open card closed and scored, untouched one withdrawn ------------
    await teacher.getByRole("button", { name: "Завершить занятие" }).click();
    await teacher.getByRole("button", { name: "Да, завершить" }).click();
    await expect(teacher.getByText("Завершено", { exact: true })).toBeVisible();
    await expect(tile).toContainText("закрыто 2");
    // The open card was closed and scored by the teacher's action.
    await expect(student.getByRole("status").filter({ hasText: "Работа с карточкой завершена" })).toBeVisible({ timeout: LIVE_MS + 3000 });
    await student.goto(`/student/sessions/${sessionId}/journal`);
    await expect(student.locator("tr[data-attempt]")).toHaveCount(2);
    await teacher.screenshot({ path: `${SHOTS}/07-monitor-finished.png`, fullPage: true });

    await teacher.getByRole("link", { name: "Отчёт" }).click();
    await expect(teacher.getByRole("heading", { name: `Отчёт: ${title}` })).toBeVisible();
    const reportRow = teacher.locator("tr[data-student=student2]");
    await expect(reportRow).toContainText("2");
    await reportRow.getByRole("button", { name: /Попытки/ }).click();
    const attempts = teacher.getByRole("table", { name: "Попытки" });
    await expect(attempts.getByRole("row")).toHaveCount(3); // header + 2 attempts
    await expect(attempts).toContainText("38260311");
    await expect(attempts).toContainText("38261102");
    await teacher.screenshot({ path: `${SHOTS}/08-report.png`, fullPage: true });
    await attempts.getByRole("link", { name: "Разбор" }).first().click();
    await expect(teacher.getByTestId("review-verdict")).toBeVisible();
    await teacher.screenshot({ path: `${SHOTS}/09-review-teacher.png`, fullPage: true });

    // --- a colleague sees 403 -------------------------------------------------------------
    const other = await newPage(browser);
    await loginWithForm(other, "teacher2");
    await expect(other.getByRole("heading", { name: "Занятия" })).toBeVisible();
    await expect(other.getByText(title)).toHaveCount(0);
    await other.goto(sessionUrl);
    await expect(other.getByRole("alert")).toContainText("другого преподавателя");
    await other.context().close();

    expect(teacherNet.problems, teacherNet.problems.join("\n")).toEqual([]);
    expect(studentNet.problems, studentNet.problems.join("\n")).toEqual([]);
    expect(teacherNet.foreign).toEqual([]);
    expect(studentNet.foreign).toEqual([]);
    await teacher.context().close();
    await student.context().close();
  });

  test("группы: создание и состав; кабинет читается на 390 px", async ({ browser }) => {
    const page = await newPage(browser, { width: 390, height: 844 });
    const net = watchNetwork(page);
    await page.goto("/login");
    await page.getByRole("button", { name: "Войти как преподаватель" }).click();
    await expect(page.getByRole("heading", { name: "Занятия" })).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/10-mobile-sessions.png`, fullPage: true });
    const scrollWidth = await page.locator("html").evaluate((el) => el.scrollWidth);
    expect(scrollWidth).toBeLessThanOrEqual(390);

    // The running/finished session from the previous test: monitoring in one column.
    const first = page.getByRole("list", { name: "Список занятий" }).getByRole("link").first();
    await first.click();
    await expect(page.getByRole("list", { name: "Обучающиеся" })).toBeVisible();
    const tiles = page.getByRole("list", { name: "Обучающиеся" }).getByRole("listitem");
    const boxes = await tiles.evaluateAll((items) => items.map((el) => el.getBoundingClientRect().left));
    expect(new Set(boxes.map((x) => Math.round(x))).size).toBe(1);
    await page.screenshot({ path: `${SHOTS}/11-mobile-monitor.png`, fullPage: true });

    await page.goto("/teacher/groups");
    await expect(page.getByRole("heading", { name: "Группы" })).toBeVisible();
    await expect(page.locator("[data-group=Учебная-1]")).toContainText("6 обучающихся");
    await page.getByRole("button", { name: "Создать группу" }).click();
    const form = page.getByRole("form", { name: "Новая группа" });
    const name = `Вечерняя ${Date.now() % 10000}`;
    await form.getByLabel("Название").fill(name);
    await form.getByRole("checkbox").nth(0).check();
    await form.getByRole("checkbox").nth(1).check();
    await form.getByRole("button", { name: "Создать группу" }).click();
    await expect(form).toBeHidden();
    await expect(page.locator(`[data-group="${name}"]`)).toContainText("2 обучающихся");
    await page.screenshot({ path: `${SHOTS}/12-mobile-groups.png`, fullPage: true });
    expect(net.problems, net.problems.join("\n")).toEqual([]);
    await page.context().close();
  });
});
