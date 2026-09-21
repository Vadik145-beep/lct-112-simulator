import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Wave 8 acceptance ("Проверка" of plan/wave-08.md): the teacher generates a scenario by a
// phrase, listens to and edits replies, approves them, asks for a revision, talks to the
// caller before approval. Screenshots go to docs/screenshots/wave-08/.

const SHOTS = "../docs/screenshots/wave-08";
const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";

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


async function apiToken(request: APIRequestContext, login: string): Promise<string> {
  const res = await request.post("/api/auth/login", { data: { login, password: PASSWORD } });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { access_token: string }).access_token;
}

// The gas-pipe card of the seeded first call (see e2e/call_intake.spec.ts), saved by the trainee.
const STUDENT_CARD = {
  signs_path: ["Запах газа в помещении", "От газововго оборудования"],
  incident_type: "13.2.4.0",
  flags: { injured: false, no_access: false, threat: false },
  services: ["101", "mosgaz", "mayor_office", "territorial_oiv"],
  address: { street: "улица Вавилова", house: "81", building: "1", apartment: "5", entrance: "1", floor: "2", code: "5В", okrug: "ЮЗАО", district: "Ломоносовский район" },
  caller: { name: "Петров Иван Сергеевич", role: "жилец", phone: "916-320-12-83" },
  description: "Свист от газовой трубы в квартире, запах газа. 03 не требуется.",
};

test.describe("Волна 8: сценарии", () => {
  test("библиотека, генерация по фразе, реплики, утверждение, переделка, разговор", async ({ page }) => {
    test.setTimeout(240_000);
    const net = watchNetwork(page);
    await loginWithForm(page, "teacher1");
    await page.getByRole("link", { name: "Сценарии" }).first().click();
    await expect(page.getByRole("heading", { name: "Сценарии" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("table", { name: "Список сценариев" })).toBeVisible({ timeout: 30_000 });
    await page.screenshot({ path: `${SHOTS}/01-library.png`, fullPage: true });

    // Filters narrow the table.
    await page.getByLabel("Режим").selectOption("card_response");
    await expect(page.getByRole("table", { name: "Список сценариев" }).locator("tbody tr").first()).toContainText("Реагирование");
    await page.getByLabel("Режим").selectOption("");
    await page.getByLabel("Билет").fill("2-1");
    const rows = page.getByRole("table", { name: "Список сценариев" }).locator("tbody tr");
    await expect(rows.first()).toContainText("2-1");
    await expect(rows.filter({ hasNotText: "2-1" })).toHaveCount(0);
    await page.getByLabel("Билет").fill("");

    // Generation by a phrase → the card of the new scenario opens.
    await page.getByRole("button", { name: "Сгенерировать по фразе" }).click();
    const form = page.getByTestId("generate-form");
    await form.getByLabel("Что должно случиться").fill("пожар в подземном паркинге, звонит ребёнок");
    await form.getByLabel("Персонаж заявителя").selectOption("child");
    const started = Date.now();
    await form.getByRole("button", { name: "Сгенерировать" }).click();
    await expect(page).toHaveURL(/\/teacher\/scenarios\/[0-9a-f-]{36}$/, { timeout: 130_000 });
    const generationMs = Date.now() - started;
    console.log(`generation by phrase: ${generationMs} ms`);
    expect(generationMs).toBeLessThan(120_000);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(/паркинг/i);
    await expect(page.getByText("На проверке")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/02-generated-card.png`, fullPage: true });

    // Replies: edit one, approve it, the text is now locked.
    const replies = page.getByTestId("replies");
    const first = replies.locator("li[data-reply]").first();
    const replyId = await first.getAttribute("data-reply");
    await first.getByRole("button", { name: `Изменить реплику ${replyId}` }).click();
    await first.getByLabel("Текст реплики").fill("Алло, тут машина горит внизу, на парковке!");
    await first.getByRole("button", { name: "Сохранить" }).click();
    // The editor closes when the server confirms the change; only then approve (a lock race otherwise).
    await expect(first.getByLabel("Текст реплики")).toBeHidden({ timeout: 30_000 });
    await expect(first).toContainText("машина горит внизу");
    await first.getByLabel(`Отметить реплику ${replyId}`).check();
    await replies.getByRole("button", { name: /Утвердить отмеченные/ }).click();
    // Approval starts Piper voicing in the background; on a laptop the CPU is busy for a while.
    await expect(first.getByLabel("Утверждена")).toBeVisible({ timeout: 30_000 });
    await expect(first.getByRole("button", { name: `Изменить реплику ${replyId}` })).toHaveCount(0);

    // Talk to the caller before approval.
    const preview = page.getByTestId("preview-dialog");
    await preview.getByLabel("Ваша фраза").fill("Скажите адрес");
    await preview.getByRole("button", { name: "Сказать" }).click();
    // Without the ai profile the dialog provider first probes llm-dialog (DNS timeout) and
    // falls back to the keyword path; Piper may be voicing the approved reply meanwhile.
    await expect(preview.getByText("Заявитель:").nth(1)).toBeVisible({ timeout: 60_000 });
    await page.screenshot({ path: `${SHOTS}/03-replies-preview.png`, fullPage: true });

    // Revision: version 2 keeps the approved reply.
    const revise = page.getByTestId("revise");
    await revise.getByLabel("Что исправить").fill("Сделай заявителя более растерянным");
    await revise.getByRole("button", { name: "Переделать" }).click();
    await expect(page.getByText("Версия 2", { exact: false }).first()).toBeVisible({ timeout: 130_000 });
    await expect(page.getByText("Сделай заявителя более растерянным").first()).toBeVisible();
    await expect(replies.locator(`li[data-reply="${replyId}"]`)).toContainText("машина горит внизу");
    await expect(replies.locator(`li[data-reply="${replyId}"]`).getByLabel("Утверждена")).toBeVisible();

    // Approve everything: the scenario is approved and the lesson can use it. With LanguageTool
    // on the stand the template replies get remarks and the page asks to confirm (plan, wave 8).
    await page.getByRole("button", { name: "Утвердить целиком" }).click();
    const confirmAnyway = page.getByRole("button", { name: "Утвердить всё равно" });
    // A cold LanguageTool takes a while for the first texts (plan, wave 4 note).
    await expect(page.getByText("Утверждён", { exact: true }).or(confirmAnyway)).toBeVisible({ timeout: 120_000 });
    if (await confirmAnyway.isVisible()) {
      await expect(page.getByTestId("grammar-report")).toBeVisible({ timeout: 60_000 });
      await confirmAnyway.click();
    }
    await expect(page.getByText("Утверждён", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("reference").getByText("утверждён")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/04-approved.png`, fullPage: true });

    // Grammar check answers (with or without LanguageTool).
    await page.getByRole("button", { name: "Проверить грамотность" }).click();
    await expect(page.getByTestId("grammar-report")).toBeVisible({ timeout: 60_000 });

    expect(net.foreign).toEqual([]);
    expect(net.problems).toEqual([]);
  });

  test("телефон: библиотека списком", async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    await loginWithForm(page, "teacher1");
    // Right after the previous test the backend may still be voicing replies (Piper on CPU).
    await expect(page).toHaveURL(/\/teacher/, { timeout: 30_000 });
    await page.goto("/teacher/scenarios");
    await expect(page.getByRole("list", { name: "Список сценариев" })).toBeVisible({ timeout: 15_000 });
    await page.screenshot({ path: `${SHOTS}/05-mobile.png`, fullPage: true });
    await context.close();
  });
  test("карточка обучающегося → сценарий реагирования → источник занятия", async ({ page, request }) => {
    test.setTimeout(240_000);
    // The trainee's part goes through the API (the UI path is e2e/call_intake.spec.ts).
    const student = await apiToken(request, "student1");
    const studentHeaders = { Authorization: `Bearer ${student}` };
    const assignments = (await (await request.get("/api/me/assignments", { headers: studentHeaders })).json()) as {
      id: string;
      status: string;
      mode: string;
    }[];
    const lesson = assignments.find((a) => a.status === "running" && a.mode === "call_intake");
    expect(lesson, "seed must provide a running call-intake session for student1").toBeTruthy();
    expect((await request.post(`/api/sessions/${lesson!.id}/restart`, { headers: studentHeaders })).status()).toBe(204);
    const journal = (await (await request.get(`/api/sessions/${lesson!.id}/journal`, { headers: studentHeaders })).json()) as {
      items: { attempt_id: string; state: string }[];
    };
    const attemptId = journal.items.find((i) => i.state !== "evaluated")!.attempt_id;
    const submitted = await request.post(`/api/attempts/${attemptId}/submit`, {
      headers: studentHeaders,
      data: { card: STUDENT_CARD, client_submission_id: `e2e-${Date.now()}` },
    });
    expect(submitted.ok(), await submitted.text()).toBeTruthy();

    await loginWithForm(page, "teacher1");
    // Right after the previous test the backend may still be voicing replies (Piper on CPU).
    await expect(page).toHaveURL(/\/teacher/, { timeout: 30_000 });
    await page.goto(`/teacher/attempts/${attemptId}/review`);
    await page.getByRole("button", { name: "Сделать сценарием реагирования" }).click();
    await expect(page).toHaveURL(/\/teacher\/scenarios\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    await expect(page.getByText("Карточка обучающегося:")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("карточка обучающегося");
    await expect(page.getByTestId("reference")).toContainText("Принять");
    await page.screenshot({ path: `${SHOTS}/06-student-card-scenario.png`, fullPage: true });
    await page.getByRole("button", { name: "Утвердить целиком" }).click();
    const confirmStudent = page.getByRole("button", { name: "Утвердить всё равно" });
    await expect(page.getByText("Утверждён", { exact: true }).or(confirmStudent)).toBeVisible({ timeout: 120_000 });
    if (await confirmStudent.isVisible()) await confirmStudent.click();
    await expect(page.getByText("Утверждён", { exact: true })).toBeVisible({ timeout: 60_000 });

    // The lesson form offers the trainees' cards as a source.
    await page.goto("/teacher/sessions/new");
    const source = page.getByLabel("Источник карточек");
    await source.selectOption("student_made");
    await expect(source).toHaveValue("student_made");
    await expect(page.getByText(/Карточки обучающихся — сохранённые в приёме вызова/)).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/07-session-source.png` });
  });

  test("удаление: черновик исчезает, использованный уходит в архив и восстанавливается", async ({ page, request }) => {
    const teacher = await apiToken(request, "teacher1");
    const headers = { Authorization: `Bearer ${teacher}` };
    // A seed scenario the running lesson has already issued to student1: it can only be archived.
    // A previous run may have left it archived — restore first so the test is repeatable.
    const findSeed = async (status: string) =>
      ((await (await request.get(`/api/scenarios?kind=call_intake&status=${status}`, { headers })).json()) as {
        items: { id: string; ticket_ref: string | null }[];
      }).items.find((s) => s.ticket_ref === "31-3");
    let used = await findSeed("approved");
    if (!used) {
      used = await findSeed("archived");
      expect(used, "seed scenario 31-3 must exist").toBeTruthy();
      expect((await request.post(`/api/scenarios/${used!.id}/restore`, { headers })).status()).toBe(200);
    }
    // A fresh draft nobody trained on (a copy of the seed body): it is deleted for good.
    const seed = (await (await request.get(`/api/scenarios/${used!.id}`, { headers })).json()) as {
      body: Record<string, unknown>;
    };
    const made = await request.post("/api/scenarios", {
      headers,
      data: { body: { ...seed.body, title: "Черновик на удаление", ticket_ref: null, approved: {} }, status: "draft" },
    });
    expect(made.status(), await made.text()).toBe(201);
    const draft = (await made.json()) as { id: string };

    await loginWithForm(page, "teacher1");
    await expect(page).toHaveURL(/\/teacher/, { timeout: 30_000 });
    await page.goto(`/teacher/scenarios/${draft.id}`);
    await page.getByRole("button", { name: "Удалить" }).click();
    await expect(page.getByRole("alertdialog", { name: "Удаление сценария" })).toBeVisible();
    await page.getByRole("button", { name: "Да, удалить" }).click();
    await expect(page).toHaveURL(/\/teacher\/scenarios$/);
    expect((await request.get(`/api/scenarios/${draft.id}`, { headers })).status()).toBe(404);

    await page.goto(`/teacher/scenarios/${used!.id}`);
    await page.getByRole("button", { name: "Удалить" }).click();
    await page.getByRole("button", { name: "Да, удалить" }).click();
    await expect(page.getByText("В архиве", { exact: true })).toBeVisible();
    await expect(page.getByText(/Сценарий в архиве/)).toBeVisible();
    // Editors are inert while archived: clicks on «Добавить» do nothing (no form opens).
    await page.getByRole("button", { name: "Добавить" }).click({ force: true }).catch(() => undefined);
    await expect(page.locator("#new-text")).toHaveCount(0);
    await page.screenshot({ path: `${SHOTS}/08-scenario-archived.png` });
    await page.getByRole("button", { name: "Восстановить" }).click();
    await expect(page.getByText("Утверждён", { exact: true })).toBeVisible();
  });
});
