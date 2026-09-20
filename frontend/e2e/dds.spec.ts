import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Wave 3 acceptance ("Проверка" and "Показать" of plan/wave-03.md) against the compose stand.
// Uses the seeded running session of student1 (управа) and resets it through the demo-only
// restart endpoint so the run is repeatable. Screenshots go to docs/screenshots/wave-03/.

const SHOTS = "../docs/screenshots/wave-03";
const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";
const NORM_SECONDS = 30;

test.describe.configure({ mode: "serial" });

async function apiToken(request: APIRequestContext, login: string): Promise<string> {
  const res = await request.post("/api/auth/login", { data: { login, password: PASSWORD } });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { access_token: string }).access_token;
}

async function resetDemoSession(request: APIRequestContext): Promise<string> {
  const token = await apiToken(request, "student1");
  const headers = { Authorization: `Bearer ${token}` };
  const assignments = (await (await request.get("/api/me/assignments", { headers })).json()) as { id: string; status: string; mode: string }[];
  // The seed also runs a call-intake lesson (wave 7): take the card one.
  const running = assignments.find((a) => a.status === "running" && a.mode === "card_response");
  expect(running, "seed must provide a running session for student1").toBeTruthy();
  const reset = await request.post(`/api/sessions/${running!.id}/restart`, { headers });
  expect(reset.status()).toBe(204);
  return running!.id;
}

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

async function loginAsStudent(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "Войти как обучающийся" }).click();
  await expect(page.getByRole("heading", { name: "Мои задания" })).toBeVisible();
}

/** Opens the status editor with the pencil and fills the row. */
async function setStatus(
  page: Page,
  status: string,
  opts: { orderNumber?: string; comment?: string; reason?: string } = {},
) {
  await page.getByRole("button", { name: "Проставить статус" }).click();
  const form = page.getByRole("form", { name: "Проставление статуса" });
  await form.getByLabel("Статус", { exact: true }).selectOption({ label: status });
  if (opts.reason) await form.getByLabel("Причина отказа").selectOption({ label: opts.reason });
  if (opts.orderNumber) await form.getByLabel("Номер наряда").fill(opts.orderNumber);
  if (opts.comment) await form.getByLabel("Комментарий", { exact: true }).fill(opts.comment);
  await form.getByRole("button", { name: "Сохранить статус" }).click();
  await expect(form).toBeHidden();
}

test.describe("Волна 3: журнал ДДС и карточка", () => {
  test("полный проход: журнал → карточка → цепочка статусов → разбор; вторая карточка «Не принята»", async ({ page, request }) => {
    test.setTimeout(180_000);
    const sessionId = await resetDemoSession(request);
    const { problems, foreign } = watchNetwork(page);

    await loginAsStudent(page);
    await page.screenshot({ path: `${SHOTS}/01-assignments.png`, fullPage: true });
    await page.getByRole("link", { name: /Открыть журнал АРМ-112/ }).click();
    await expect(page).toHaveURL(new RegExp(`/student/sessions/${sessionId}/journal$`));

    // Difficulty 3: three cards at once, all «Добавлена» / «Зарегистрирована».
    const rows = page.locator("tr[data-attempt]");
    await expect(rows).toHaveCount(3);
    await expect(page.getByText("Список происшествий")).toBeVisible();
    await expect(page.getByText("задымление: мусоропровод").first()).toBeVisible();
    await page.getByRole("button", { name: "Раскрыть" }).first().click();
    await expect(page.getByText("Заявитель:")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/02-journal.png`, fullPage: true });

    // Card 38260311: full chain within the norm.
    await page.getByRole("link", { name: /Открыть карточку 38260311/ }).click();
    await expect(page.getByText("Происшествие 38260311")).toBeVisible();
    await expect(page.getByTestId("own-service-panel")).toContainText("Получена службой");
    await page.screenshot({ path: `${SHOTS}/03-card.png`, fullPage: true });

    // Only «Принята» / «Не принята» are offered first (memo page 24).
    await page.getByRole("button", { name: "Проставить статус" }).click();
    const statusSelect = page.getByRole("form", { name: "Проставление статуса" }).getByLabel("Статус", { exact: true });
    await expect(statusSelect.locator("option")).toHaveText(["Принята", "Не принята"]);
    await page.screenshot({ path: `${SHOTS}/04-status-editor.png`, fullPage: true });
    await page.getByRole("button", { name: "Отменить" }).click();

    // Keyboard: Alt+A picks «Принята», Ctrl+Enter saves.
    await page.keyboard.press("Alt+A");
    await expect(statusSelect).toHaveValue("accepted");
    await page.keyboard.press("Control+Enter");
    await expect(page.getByTestId("own-service-panel")).toContainText("Принята");
    await expect(page.getByTestId("own-service-tab")).toContainText("Принята");

    await setStatus(page, "Начало реагирования", { orderNumber: "14-217", comment: "Направлен дежурный слесарь, наряд 14-217" });
    await expect(page.getByTestId("own-service-panel")).toContainText("наряд 14-217");
    await setStatus(page, "Прибытие");
    await setStatus(page, "Проведение работ", { comment: "Мусоропровод вскрыт, тлеющий мусор удалён" });
    await setStatus(page, "Работы завершены", { comment: "Задымление устранено, ствол промыт, пострадавших нет" });

    // Closed and evaluated at once.
    const status = page.getByRole("status").filter({ hasText: "Работа с карточкой завершена" });
    await expect(status).toBeVisible();
    await expect(page.getByTestId("card-score")).toHaveText(/^\d+$/);
    await expect(page.getByRole("button", { name: "Проставить статус" })).toBeDisabled();
    await page.screenshot({ path: `${SHOTS}/05-card-closed.png`, fullPage: true });

    await page.getByRole("link", { name: "Открыть разбор" }).click();
    await expect(page.getByTestId("review-verdict")).toHaveText("Зачтено");
    const total = Number(await page.getByTestId("review-total").textContent());
    expect(total).toBeGreaterThanOrEqual(90);
    await expect(page.getByText("Цепочка статусов совпала с эталоном.")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/06-review.png`, fullPage: true });

    // Second card (the duplicate): «Не принята» with a reason and a comment.
    await page.getByRole("link", { name: "Следующая карточка" }).click();
    await page.getByRole("link", { name: /Открыть карточку 38260340/ }).click();
    await expect(page.getByText("Происшествие 38260340")).toBeVisible();
    await page.getByRole("button", { name: "Проставить статус" }).click();
    const form = page.getByRole("form", { name: "Проставление статуса" });
    await form.getByLabel("Статус", { exact: true }).selectOption({ label: "Не принята" });
    await form.getByRole("button", { name: "Сохранить статус" }).click();
    await expect(form.getByRole("alert")).toContainText("причину отказа");
    await form.getByLabel("Причина отказа").selectOption({ label: "Дубль" });
    await form.getByLabel("Комментарий", { exact: true }).fill("Дубль: реагирование по КП 38260311, информация передана дежурному слесарю");
    await form.getByRole("button", { name: "Сохранить статус" }).click();
    await expect(form).toBeHidden();
    await expect(page.getByTestId("own-service-panel")).toContainText("Не принята: Дубль");
    // After «Не принята» only «Принята» remains (memo page 21).
    await page.getByRole("button", { name: "Проставить статус" }).click();
    await expect(form.getByLabel("Статус", { exact: true }).locator("option")).toHaveText(["Принята"]);
    await page.getByRole("button", { name: "Отменить" }).click();
    await page.getByRole("button", { name: "Завершить работу с карточкой" }).click();
    await page.getByRole("button", { name: "Да, завершить" }).click();
    await expect(page.getByTestId("card-score")).toBeVisible();

    // Journal: the closed card is «Завершена», the refused one is «Отказ» in red.
    await page.getByRole("link", { name: "Закрыть карточку и вернуться в журнал" }).click();
    await expect(page.locator('tr[data-card-status="finished"]')).toHaveCount(1);
    await expect(page.locator('tr[data-card-status="refused"]')).toHaveCount(1);
    await page.screenshot({ path: `${SHOTS}/07-journal-after.png`, fullPage: true });

    expect(foreign, "все запросы только к своему origin").toEqual([]);
    expect(problems).toEqual([]);
  });

  test("недопустимый переход отклоняется API", async ({ request }) => {
    const sessionId = await resetDemoSession(request);
    const token = await apiToken(request, "student1");
    const headers = { Authorization: `Bearer ${token}` };
    const journal = (await (await request.get(`/api/sessions/${sessionId}/journal`, { headers })).json()) as {
      items: { attempt_id: string }[];
    };
    const attemptId = journal.items[0].attempt_id;
    const res = await request.post(`/api/attempts/${attemptId}/status`, { headers, data: { status: "arrived" } });
    expect(res.status()).toBe(422);
    const body = (await res.json()) as { error: { code: string; message: string } };
    expect(body.error.code).toBe("not_allowed");
    expect(body.error.message).toContain("Принята");
  });

  test("карточка без действий через 30 с: «Не оповещено» красным и late_primary в разборе", async ({ page, request }) => {
    test.setTimeout(120_000);
    await resetDemoSession(request);
    await loginAsStudent(page);
    await page.getByRole("link", { name: /Открыть журнал АРМ-112/ }).click();
    const row = page.locator('tr[data-attempt]', { hasText: "38260311" });
    await expect(row.locator("[data-phase]")).toHaveAttribute("data-phase", /ok|warning/);
    await expect(row).toHaveAttribute("data-card-status", "not_notified", { timeout: (NORM_SECONDS + 10) * 1000 });
    await expect(row.getByText("Не оповещено")).toBeVisible();
    await expect(row.locator("[data-phase]")).toHaveAttribute("data-phase", "overdue");
    await page.screenshot({ path: `${SHOTS}/08-not-notified.png`, fullPage: true });

    // Late «Принята» then closing: the review names the late primary status.
    await page.getByRole("link", { name: /Открыть карточку 38260311/ }).click();
    await expect(page.getByTestId("own-service-panel")).toContainText("Получена службой");
    await page.keyboard.press("Alt+A");
    await expect(page.getByRole("form", { name: "Проставление статуса" }).getByLabel("Статус", { exact: true })).toHaveValue("accepted");
    await page.keyboard.press("Control+Enter");
    await expect(page.getByTestId("own-service-panel")).toContainText("Принята");
    await page.getByRole("button", { name: "Завершить работу с карточкой" }).click();
    await page.getByRole("button", { name: "Да, завершить" }).click();
    await page.getByRole("link", { name: "Открыть разбор" }).click();
    await expect(page.locator('[data-error="late_primary"]')).toBeVisible();
    await expect(page.getByTestId("review-verdict")).toHaveText("Не зачтено");
  });

  test("30 с офлайн и повтор действия: одна запись в истории; черновик переживает перезагрузку", async ({ page, request, context }) => {
    test.setTimeout(120_000);
    await resetDemoSession(request);
    await loginAsStudent(page);
    await page.getByRole("link", { name: /Открыть журнал АРМ-112/ }).click();
    await page.getByRole("link", { name: /Открыть карточку 38260311/ }).click();
    await expect(page.getByTestId("own-service-panel")).toContainText("Получена службой");

    // Draft: comment typed, page reloaded, comment still there.
    await page.getByRole("button", { name: "Проставить статус" }).click();
    const form = page.getByRole("form", { name: "Проставление статуса" });
    await form.getByLabel("Комментарий", { exact: true }).fill("черновик комментария");
    await page.reload();
    await expect(page.getByRole("form", { name: "Проставление статуса" }).getByLabel("Комментарий", { exact: true })).toHaveValue("черновик комментария");
    await page.getByRole("form", { name: "Проставление статуса" }).getByLabel("Комментарий", { exact: true }).fill("");

    // Offline for 30 s: the action waits, is sent once the network is back, and is not
    // duplicated by a second click.
    await context.setOffline(true);
    await page.getByRole("form", { name: "Проставление статуса" }).getByRole("button", { name: "Сохранить статус" }).click();
    await expect(page.getByRole("form", { name: "Проставление статуса" }).getByRole("alert")).toContainText("Нет связи");
    await page.waitForTimeout(30_000);
    await page.getByRole("form", { name: "Проставление статуса" }).getByRole("button", { name: "Сохранить статус" }).click({ force: true }).catch(() => undefined);
    await context.setOffline(false);
    await expect(page.getByRole("form", { name: "Проставление статуса" })).toBeHidden({ timeout: 15_000 });
    const history = page.getByTestId("own-service-panel").getByRole("list", { name: "История статусов" }).getByRole("listitem");
    await expect(history.filter({ hasText: "Принята" })).toHaveCount(1);
    await expect(history).toHaveCount(3); // Добавлена, Получена службой, Принята
  });

  test("всё выполнимо с клавиатуры: строка журнала открывается по Enter, ? показывает подсказку", async ({ page, request }) => {
    await resetDemoSession(request);
    await loginAsStudent(page);
    await page.getByRole("link", { name: /Открыть журнал АРМ-112/ }).click();
    await page.locator("tr[data-attempt]", { hasText: "38260311" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("Происшествие 38260311")).toBeVisible();
    await page.keyboard.press("?");
    await expect(page.getByRole("dialog", { name: "Горячие клавиши" })).toBeVisible();
    await page.getByRole("button", { name: "Закрыть" }).click();
    await expect(page.getByRole("dialog")).toBeHidden();
  });
});

test.describe("Задача #35: проверка карточки от оператора 112", () => {
  test("карточка с неверным домом: обучающийся отмечает поле, вводит верный дом, принимает карточку, в разборе видит «Проверка данных»", async ({ page, request }) => {
    test.setTimeout(180_000);
    const { problems, foreign } = watchNetwork(page);

    // A lesson of its own for student2 (an own group, so the demo lesson of student1 stays
    // untouched) with the seed card that carries a wrong house number.
    const teacher = await apiToken(request, "teacher1");
    const th = { Authorization: `Bearer ${teacher}` };
    const stamp = new Date().toLocaleTimeString("ru-RU");
    const students = (await (await request.get("/api/students", { headers: th })).json()) as { id: string; login: string }[];
    const group = await request.post("/api/groups", {
      headers: th,
      data: { title: `Группа #35 · ${stamp}`, student_ids: students.filter((s) => s.login === "student2").map((s) => s.id) },
    });
    expect(group.status(), await group.text()).toBe(201);
    const scenarios = (await (await request.get("/api/scenarios", { headers: th, params: { kind: "card_response", q: "соседний дом" } })).json()) as { items: { id: string; title: string }[] };
    expect(scenarios.items.length, "seed must provide the card with a wrong house").toBeGreaterThan(0);
    const created = await request.post("/api/sessions", {
      headers: th,
      data: {
        title: `#35: ошибка оператора · ${stamp}`,
        group_id: ((await group.json()) as { id: string }).id,
        difficulty: 3,
        scenario_ids: [scenarios.items[0]!.id],
        service_profile: ["moek"],
        norm_seconds: 30,
        pass_threshold: 70,
      },
    });
    expect(created.status(), await created.text()).toBe(201);
    const sessionId = ((await created.json()) as { id: string }).id;
    expect((await request.post(`/api/sessions/${sessionId}/start`, { headers: th })).ok()).toBeTruthy();

    await page.goto("/login");
    await page.getByLabel("Логин").fill("student2");
    await page.getByLabel("Пароль").fill(PASSWORD);
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Мои задания" })).toBeVisible();
    await page.goto(`/student/sessions/${sessionId}/journal`);
    await page.getByRole("link", { name: /Открыть карточку 38260452/ }).click();
    await expect(page.getByText("Происшествие 38260452")).toBeVisible();
    await expect(page.getByTestId("own-service-panel")).toContainText("Получена службой");

    // No hint points at the field: only the generic reminder in the training panel.
    await expect(page.getByTestId("data-check-hint")).toBeVisible();
    await expect(page.getByTestId("card-address")).toContainText("Зелёный проспект, 19");
    await expect(page.getByTestId("flag-mark")).toHaveCount(0);

    // The dispatcher flags the house and names the right one; the mark can be removed and set again.
    await page.getByRole("button", { name: "Отметить ошибку: Дом" }).click();
    const editor = page.getByRole("form", { name: /Ошибка в поле/ });
    await editor.getByLabel("Часть адреса").selectOption({ label: "Дом: 19" });
    await editor.getByLabel("Правильное значение: Дом").fill("17");
    await editor.getByRole("button", { name: "Отметить" }).click();
    await expect(editor).toBeHidden();
    await expect(page.getByTestId("flag-mark")).toContainText("верно: 17");
    await expect(page.getByTestId("flag-count")).toContainText("1");
    await page.getByRole("button", { name: "Снять отметку: Дом" }).click();
    await expect(page.getByTestId("flag-mark")).toHaveCount(0);
    await page.getByRole("button", { name: "Отметить ошибку: Дом" }).click();
    await editor.getByLabel("Часть адреса").selectOption({ label: "Дом: 19" });
    await editor.getByLabel("Правильное значение: Дом").fill("17");
    await editor.getByRole("button", { name: "Отметить" }).click();
    await expect(page.getByTestId("flag-mark")).toContainText("верно: 17");
    await page.screenshot({ path: `../docs/screenshots/wave-11/35-card-flagged.png`, fullPage: true });

    // The card itself still shows what the operator typed; the chain closes the card.
    await expect(page.getByTestId("card-address")).toContainText("Зелёный проспект, 19");
    await setStatus(page, "Принята");
    await setStatus(page, "Начало реагирования", { orderNumber: "МОЭК-4127", comment: "Направлена аварийная бригада тепловых сетей" });
    await setStatus(page, "Прибытие");
    await setStatus(page, "Проведение работ", { comment: "Проверка ИТП дома 17, запуск циркуляции" });
    await setStatus(page, "Работы завершены", { comment: "Заменён насос в ИТП, отопление в доме 17 восстановлено" });
    await expect(page.getByTestId("card-score")).toHaveText(/^\d+$/, { timeout: 60_000 });
    await expect(page.getByRole("button", { name: "Ошибка отмечена: Дом" })).toBeDisabled();

    await page.getByRole("link", { name: "Открыть разбор" }).click();
    const check = page.getByTestId("data-check");
    await expect(check).toBeVisible();
    await expect(check).toContainText("ошибка найдена и исправлена верно");
    await expect(check).toContainText("В карточке: «19», верно: «17»");
    await expect(page.getByText("Проверка данных", { exact: true }).first()).toBeVisible();
    await page.screenshot({ path: `../docs/screenshots/wave-11/35-review.png`, fullPage: true });

    expect((await request.post(`/api/sessions/${sessionId}/finish`, { headers: th })).ok()).toBeTruthy();
    expect(foreign, "все запросы только к своему origin").toEqual([]);
    expect(problems).toEqual([]);
  });
});
