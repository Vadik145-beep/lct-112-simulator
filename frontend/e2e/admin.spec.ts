import { expect, test, type Browser, type Page } from "@playwright/test";

// Wave 9 acceptance («Показать» of plan/wave-09.md) against the compose stand: the
// administrator creates a trainee, the teacher adds them to a group and runs a lesson, the
// trainee closes a card, the teacher changes the total with a reason and downloads the PDF;
// the trainee sees the struck-through total and the comment; the administrator checks the
// state of the system, the audit log and the backups. Screenshots go to docs/screenshots/wave-09/.

const SHOTS = "../docs/screenshots/wave-09";
const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";
// The first requests after a cold start (argon2, warm-up of the models) can take a while.
const COLD_MS = 60_000;

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

async function newPage(browser: Browser, viewport?: { width: number; height: number }) {
  const context = await browser.newContext(viewport ? { viewport } : {});
  return context.newPage();
}

async function loginWithForm(page: Page, login: string, password = PASSWORD) {
  await page.goto("/login");
  await page.getByLabel("Логин").fill(login);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
}

async function setStatus(page: Page, status: string, opts: { orderNumber?: string; comment?: string } = {}) {
  await page.getByRole("button", { name: "Проставить статус" }).click();
  const form = page.getByRole("form", { name: "Проставление статуса" });
  await form.getByLabel("Статус", { exact: true }).selectOption({ label: status });
  if (opts.orderNumber) await form.getByLabel("Номер наряда").fill(opts.orderNumber);
  if (opts.comment) await form.getByLabel("Комментарий", { exact: true }).fill(opts.comment);
  await form.getByRole("button", { name: "Сохранить статус" }).click();
  // The final status scores the card in the request; on a cold stand the first evaluation
  // loads LanguageTool and the embeddings model (40–60 s, see docs/PROGRESS.md of wave 7).
  await expect(form).toBeHidden({ timeout: COLD_MS });
}

test.describe("Волна 9: кабинеты", () => {
  test("администратор завёл пользователя → группа → занятие → оценка с причиной → PDF", async ({ browser }) => {
    test.setTimeout(300_000);
    const stamp = Date.now() % 100000;
    const login = `e2e-${stamp}`;
    const admin = await newPage(browser);
    const adminNet = watchNetwork(admin);

    // --- administrator: users with hidden names, creation -------------------------------
    await admin.goto("/login");
    await admin.getByRole("button", { name: "Войти как администратор" }).click();
    await expect(admin.getByRole("heading", { name: "Пользователи" })).toBeVisible({ timeout: COLD_MS });
    const teacherRow = admin.locator("tr[data-login=teacher1]");
    await expect(teacherRow.getByTestId("user-display-name")).toHaveText("И. М. П.");
    await expect(teacherRow).not.toContainText("Иванова");
    await teacherRow.getByRole("button", { name: "Показать ФИО: teacher1" }).click();
    await expect(teacherRow).toContainText("Иванова Мария Петровна");
    await admin.screenshot({ path: `${SHOTS}/01-admin-users.png`, fullPage: true });

    await admin.getByRole("button", { name: "Создать пользователя" }).click();
    const form = admin.getByRole("form", { name: "Новый пользователь" });
    await form.getByLabel("Логин", { exact: true }).fill(login);
    await form.getByLabel("ФИО").fill("Тестов Тест Тестович");
    await form.getByLabel("Роль", { exact: true }).selectOption("student");
    await form.getByLabel("Служба (для обучающегося ДДС)").selectOption("territorial_oiv");
    await form.getByRole("button", { name: "Создать пользователя" }).click();
    const secret = admin.getByTestId("admin-secret");
    await expect(secret).toContainText(`Пользователь создан: ${login}`);
    const tempPassword = (await secret.getByText(/Временный пароль:/).textContent())!.replace("Временный пароль:", "").trim();
    expect(tempPassword.length).toBeGreaterThanOrEqual(8);
    await expect(admin.locator(`tr[data-login=${login}]`)).toContainText("сменить пароль");
    await admin.screenshot({ path: `${SHOTS}/02-admin-user-created.png`, fullPage: true });

    // --- the new trainee logs in and has to change the password ----------------------------
    const student = await newPage(browser);
    const studentNet = watchNetwork(student);
    await loginWithForm(student, login, tempPassword);
    await expect(student.getByLabel("Текущий пароль")).toBeVisible({ timeout: COLD_MS });
    await student.getByLabel("Текущий пароль").fill(tempPassword);
    await student.getByLabel(/^Новый пароль \(/).fill("Trainee-2026!");
    await student.getByLabel("Новый пароль ещё раз").fill("Trainee-2026!");
    await student.getByRole("button", { name: "Сменить пароль" }).click();
    await expect(student.getByRole("heading", { name: "Мои задания" })).toBeVisible({ timeout: COLD_MS });

    // --- teacher: group with the new trainee, lesson, start ---------------------------------
    const teacher = await newPage(browser);
    const teacherNet = watchNetwork(teacher);
    await teacher.goto("/login");
    await teacher.getByRole("button", { name: "Войти как преподаватель" }).click();
    await expect(teacher.getByRole("heading", { name: "Занятия" })).toBeVisible({ timeout: COLD_MS });
    await teacher.goto("/teacher/groups");
    await teacher.getByRole("button", { name: "Создать группу" }).click();
    const groupForm = teacher.getByRole("form", { name: "Новая группа" });
    const groupTitle = `Группа волны 9 · ${stamp}`;
    await groupForm.getByLabel("Название").fill(groupTitle);
    await groupForm.getByRole("checkbox", { name: new RegExp(login) }).check();
    await groupForm.getByRole("button", { name: "Создать группу" }).click();
    await expect(teacher.locator(`[data-group="${groupTitle}"]`)).toContainText("1 обучающийся");

    await teacher.goto("/teacher/sessions/new");
    const sessionForm = teacher.getByRole("form", { name: "Новое занятие" });
    const title = `Волна 9 · ${stamp}`;
    await sessionForm.getByLabel("Название").fill(title);
    const groupSelect = sessionForm.getByLabel("Группа");
    const groupValue = await groupSelect.locator("option", { hasText: groupTitle }).getAttribute("value");
    await groupSelect.selectOption(groupValue!);
    await sessionForm.getByLabel("Территориальные ОИВ (управы, префектуры)").check();
    await sessionForm.getByRole("button", { name: "Создать занятие" }).click();
    await expect(teacher.getByRole("heading", { name: title })).toBeVisible();
    const sessionId = teacher.url().split("/sessions/")[1]!;
    await teacher.getByRole("button", { name: "Начать занятие" }).click();
    const startedAt = Date.now();
    await expect(teacher.getByText("Идёт", { exact: true })).toBeVisible({ timeout: COLD_MS });
    console.log(`session start took ${Date.now() - startedAt} ms`);

    // --- trainee closes one card with the full chain ---------------------------------------
    await student.goto(`/student/sessions/${sessionId}/journal`);
    await expect(student.locator("tr[data-attempt]").first()).toBeVisible({ timeout: 10_000 });
    await student.locator("tr[data-attempt]").first().click();
    await setStatus(student, "Принята");
    await setStatus(student, "Начало реагирования", { orderNumber: "14-217", comment: "Направлен дежурный слесарь" });
    await setStatus(student, "Прибытие");
    await setStatus(student, "Проведение работ", { comment: "Мусоропровод вскрыт" });
    await setStatus(student, "Работы завершены", { comment: "Задымление устранено, пострадавших нет" });
    await expect(student.getByTestId("card-score")).toHaveText(/^\d+$/, { timeout: COLD_MS });

    await teacher.getByRole("button", { name: "Завершить занятие" }).click();
    await teacher.getByRole("button", { name: "Да, завершить" }).click();
    await expect(teacher.getByText("Завершено", { exact: true })).toBeVisible();

    // --- teacher: override with a reason, comment, PDF ----------------------------------------
    await teacher.goto(`/teacher/sessions/${sessionId}/report`);
    await expect(teacher.getByRole("heading", { name: `Отчёт: ${title}` })).toBeVisible();
    const reportRow = teacher.locator(`tr[data-student=${login}]`);
    await reportRow.getByRole("button", { name: /Попытки/ }).click();
    await teacher.getByRole("table", { name: "Попытки" }).getByRole("link", { name: "Разбор" }).first().click();
    await expect(teacher.getByTestId("review-verdict")).toBeVisible();
    const originalTotal = await teacher.getByTestId("review-total").textContent();

    await teacher.getByRole("button", { name: "Изменить оценку" }).click();
    const overrideForm = teacher.getByRole("form", { name: "Изменение оценки" });
    await overrideForm.getByLabel("Новый итог").fill("64");
    // Without a reason the button stays disabled.
    await expect(overrideForm.getByRole("button", { name: "Сохранить новую оценку" })).toBeDisabled();
    await overrideForm.getByLabel("Причина (обязательно)").fill("Комментарий к работам не по существу");
    await overrideForm.getByRole("button", { name: "Сохранить новую оценку" }).click();
    await expect(teacher.getByTestId("review-total")).toHaveText("64");
    await expect(teacher.getByTestId("review-old-total")).toHaveText(originalTotal!);
    await expect(teacher.getByTestId("review-override-reason")).toContainText("Комментарий к работам не по существу");
    await expect(teacher.getByTestId("review-verdict")).toHaveText("Не зачтено");

    const commentForm = teacher.getByRole("form", { name: "Новый комментарий" });
    await commentForm.getByLabel("Комментарий обучающемуся").fill("Наряд указывайте в поле «Номер наряда».");
    await commentForm.getByRole("button", { name: "Отправить комментарий" }).click();
    await expect(teacher.getByTestId("review-comments")).toContainText("Наряд указывайте в поле «Номер наряда».");
    await teacher.screenshot({ path: `${SHOTS}/03-review-override.png`, fullPage: true });

    await teacher.goto(`/teacher/sessions/${sessionId}/report`);
    await reportRow.getByRole("button", { name: /Попытки/ }).click();
    await expect(teacher.getByRole("table", { name: "Попытки" })).toContainText("изменена");
    const download = teacher.waitForEvent("download");
    await teacher.getByRole("group", { name: "Скачать отчёт" }).getByRole("button", { name: "PDF" }).click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(/\.pdf$/);
    const path = await file.path();
    expect(path).toBeTruthy();
    await teacher.screenshot({ path: `${SHOTS}/04-report-export.png`, fullPage: true });

    // --- trainee sees the struck-through total, the reason and the comment ----------------
    await student.goto("/student/progress");
    await expect(student.getByRole("heading", { name: "Мой прогресс" })).toBeVisible();
    await expect(student.getByRole("table", { name: "Занятия" })).toContainText(title);
    await expect(student.getByRole("table", { name: "Занятия" })).toContainText("оценка изменена");
    await student.screenshot({ path: `${SHOTS}/05-student-progress.png`, fullPage: true });
    await student.goto(`/student/sessions/${sessionId}/journal`);
    await student.locator("tr[data-attempt]").first().click();
    await student.getByRole("link", { name: "Открыть разбор" }).click();
    await expect(student.getByTestId("review-total")).toHaveText("64");
    await expect(student.getByTestId("review-old-total")).toHaveText(originalTotal!);
    await expect(student.getByTestId("review-override-reason")).toContainText("Комментарий к работам не по существу");
    await expect(student.getByTestId("review-comments")).toContainText("Наряд указывайте");
    await student.screenshot({ path: `${SHOTS}/06-student-review.png`, fullPage: true });

    // --- administrator: state, audit with the override, backups, settings ------------------
    await admin.goto("/admin/health");
    await expect(admin.getByRole("heading", { name: "Состояние системы" })).toBeVisible();
    await expect(admin.locator("[data-service=postgres]")).toHaveAttribute("data-status", "ok");
    await expect(admin.locator("[data-service=redis]")).toHaveAttribute("data-status", "ok");
    await expect(admin.locator("[data-service=languagetool]")).toHaveAttribute("data-status", "ok", { timeout: 20_000 });
    await admin.screenshot({ path: `${SHOTS}/07-admin-health.png`, fullPage: true });

    await admin.goto("/admin/audit");
    await admin.getByRole("button", { name: "Проверить целостность" }).click();
    await expect(admin.getByTestId("audit-verify")).toContainText("Цепочка целая");
    await admin.getByLabel("Действие").selectOption("evaluation.override");
    await expect(admin.getByRole("table", { name: "Записи аудита" })).toContainText("Комментарий к работам не по существу");
    await admin.getByLabel("Действие").selectOption("user.reveal");
    await expect(admin.getByRole("table", { name: "Записи аудита" })).toContainText("user.reveal");
    await admin.screenshot({ path: `${SHOTS}/08-admin-audit.png`, fullPage: true });

    await admin.goto("/admin/backups");
    await expect(admin.getByRole("heading", { name: "Резервные копии" })).toBeVisible();
    const makeNow = admin.getByRole("button", { name: "Сделать копию сейчас" });
    await expect(makeNow).toBeEnabled({ timeout: 70_000 });
    await makeNow.click();
    await expect(admin.locator("tr[data-status=done]").first()).toBeVisible({ timeout: 60_000 });
    await admin.screenshot({ path: `${SHOTS}/09-admin-backups.png`, fullPage: true });

    await admin.goto("/admin/settings");
    await admin.getByLabel("Уровень журнала API").selectOption("INFO");
    await admin.getByRole("button", { name: "Сохранить настройки" }).click();
    await expect(admin.getByRole("status").filter({ hasText: "Сохранено" })).toBeVisible();
    await admin.screenshot({ path: `${SHOTS}/10-admin-settings.png`, fullPage: true });

    // The administrator has no way into a lesson: the teacher's routes answer 403.
    const adminLogin = await admin.request.post("/api/auth/demo/admin");
    const adminToken = ((await adminLogin.json()) as { access_token: string }).access_token;
    const withToken = await admin.request.post(`/api/sessions/${sessionId}/finish`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(withToken.status()).toBe(403);

    expect(adminNet.problems, adminNet.problems.join("\n")).toEqual([]);
    expect(teacherNet.problems, teacherNet.problems.join("\n")).toEqual([]);
    expect(studentNet.problems, studentNet.problems.join("\n")).toEqual([]);
    expect([...adminNet.foreign, ...teacherNet.foreign, ...studentNet.foreign]).toEqual([]);
    await admin.context().close();
    await teacher.context().close();
    await student.context().close();
  });

  test("заголовки безопасности и кабинет администратора на 390 px", async ({ browser, request }) => {
    const res = await request.get("/login");
    const headers = res.headers();
    expect(headers["strict-transport-security"]).toContain("max-age=");
    expect(headers["content-security-policy"]).toContain("default-src 'self'");
    expect(headers["x-frame-options"]).toBe("DENY");
    expect(headers["x-content-type-options"]).toBe("nosniff");

    const page = await newPage(browser, { width: 390, height: 844 });
    await page.goto("/login");
    await page.getByRole("button", { name: "Войти как администратор" }).click();
    await expect(page.getByRole("heading", { name: "Пользователи" })).toBeVisible({ timeout: COLD_MS });
    const scrollWidth = await page.locator("html").evaluate((el) => el.scrollWidth);
    expect(scrollWidth).toBeLessThanOrEqual(390);
    await page.screenshot({ path: `${SHOTS}/11-mobile-admin.png`, fullPage: true });
    await page.context().close();
  });
});
