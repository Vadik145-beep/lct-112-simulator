import { expect, test, type APIRequestContext, type Browser, type Page } from "@playwright/test";

// Issue #265 «Задания формирует ИИ» against the compose stand: the teacher only ticks the box,
// picks the group and the difficulty and starts; two trainees get different cards written for
// them; one closes the card and reads the advice and why the card was chosen; the teacher sees
// the lesson's advice in the report. Without a generation model the cards are written by the
// template, which is the path the test relies on.

const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";
// A card is written in the background: by the template at once, by a model in seconds.
const WRITTEN_MS = 60_000;

test.describe.configure({ mode: "serial" });

async function token(request: APIRequestContext, login: string): Promise<Record<string, string>> {
  const r = await request.post("/api/auth/login", { data: { login, password: PASSWORD } });
  expect(r.ok()).toBeTruthy();
  return { Authorization: `Bearer ${((await r.json()) as { access_token: string }).access_token}` };
}

async function loginWithForm(page: Page, login: string) {
  await page.goto("/login");
  await page.getByLabel("Логин").fill(login);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login$/);
}

async function newPage(browser: Browser) {
  return (await browser.newContext()).newPage();
}

async function journalTitles(request: APIRequestContext, login: string, sessionId: string) {
  const r = await request.get(`/api/sessions/${sessionId}/journal`, { headers: await token(request, login) });
  expect(r.ok()).toBeTruthy();
  return ((await r.json()) as { items: { incident_title: string }[] }).items.map((i) => i.incident_title);
}

test("«Задания формирует ИИ»: разные карточки двум обучающимся, советы после карточки и по занятию", async ({
  browser,
  request,
}) => {
  test.setTimeout(240_000);
  const stamp = new Date().toLocaleTimeString("ru-RU");
  const title = `Задания ИИ · ${stamp}`;

  // Own group of two trainees: other suites drive student1's demo lessons.
  const headers = await token(request, "teacher1");
  const students = (await (await request.get("/api/students", { headers })).json()) as { id: string; login: string }[];
  const groupTitle = `Группа ИИ · ${stamp}`;
  const group = await request.post("/api/groups", {
    headers,
    data: { title: groupTitle, student_ids: students.filter((s) => ["student4", "student5"].includes(s.login)).map((s) => s.id) },
  });
  expect(group.status()).toBe(201);

  // --- teacher: tick the box, group, difficulty, start -------------------------------------
  const teacher = await newPage(browser);
  await loginWithForm(teacher, "teacher1");
  await teacher.goto("/teacher/sessions/new");
  const form = teacher.getByRole("form", { name: "Новое занятие" });
  await form.getByLabel("Название").fill(title);
  const groupSelect = form.getByLabel("Группа");
  await groupSelect.selectOption((await groupSelect.locator("option", { hasText: groupTitle }).getAttribute("value"))!);
  await form.getByLabel("Задания формирует ИИ").check();
  await expect(form.getByText(/готовые сценарии не нужны/)).toBeVisible();
  await form.getByLabel("Сложность").selectOption("2");
  await expect(form.getByText("Каждый обучающийся получит 5 своих карточек по одной.")).toBeVisible();
  await form.getByRole("button", { name: "Создать занятие" }).click();
  await expect(teacher.getByRole("heading", { name: title })).toBeVisible();
  const sessionId = teacher.url().split("/sessions/")[1]!.split("/")[0]!;
  await teacher.getByRole("button", { name: "Начать занятие" }).click();
  await expect(teacher.getByText("Идёт", { exact: true })).toBeVisible();

  // --- two trainees: each gets one card of his own, and they differ -------------------------
  const first = await newPage(browser);
  const second = await newPage(browser);
  for (const [page, login] of [
    [first, "student4"],
    [second, "student5"],
  ] as const) {
    await loginWithForm(page, login);
    await page.goto(`/student/sessions/${sessionId}/journal`);
    await expect(page.locator("tr[data-attempt]")).toHaveCount(1, { timeout: WRITTEN_MS });
  }
  const [a, b] = [await journalTitles(request, "student4", sessionId), await journalTitles(request, "student5", sessionId)];
  expect(a).toHaveLength(1);
  expect(b).toHaveLength(1);
  expect(a[0]).not.toEqual(b[0]);

  // --- the first trainee takes the card and closes it: the review advises -------------------
  await first.getByRole("link", { name: /Открыть карточку/ }).first().click();
  await expect(first.getByText(/^Происшествие \d+/)).toBeVisible();
  await first.getByRole("button", { name: "Проставить статус" }).click();
  const statusForm = first.getByRole("form", { name: "Проставление статуса" });
  await statusForm.getByLabel("Статус", { exact: true }).selectOption({ label: "Принята" });
  await statusForm.getByRole("button", { name: "Сохранить статус" }).click();
  await expect(statusForm).toBeHidden({ timeout: 15_000 });
  // The training panel is folded by default (#275): it holds «Завершить работу с карточкой».
  await first.getByRole("button", { name: "Развернуть панель тренажёра" }).click();
  await first.getByRole("button", { name: "Завершить работу с карточкой" }).click();
  await first.getByRole("button", { name: "Да, завершить" }).click();
  await first.getByRole("link", { name: "Открыть разбор" }).click();
  const advice = first.getByTestId("review-advice");
  await expect(advice).toBeVisible();
  await expect(advice.getByText("Советы для дальнейшего обучения")).toBeVisible();
  await expect(advice.getByText("Почему ИИ выдал эту карточку")).toBeVisible();
  await expect(advice.locator("li").first()).toBeVisible();

  // The next card is written while the trainee reads the review and reaches the journal.
  await first.goto(`/student/sessions/${sessionId}/journal`);
  await expect(first.locator("tr[data-attempt]")).toHaveCount(2, { timeout: WRITTEN_MS });

  // --- teacher: the lesson's advice per trainee in the report -------------------------------
  await teacher.goto(`/teacher/sessions/${sessionId}/report`);
  await expect(teacher.getByText("Советы по занятию").first()).toBeVisible();
});
