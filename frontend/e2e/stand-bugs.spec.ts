import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Regressions found by hand on the stand (docs/BUGS.md), against the compose stand in demo
// mode: the seeded lessons of student1 are reset through the demo-only restart endpoint.

const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";

test.describe.configure({ mode: "serial" });

async function apiToken(request: APIRequestContext, login: string): Promise<string> {
  const res = await request.post("/api/auth/login", { data: { login, password: PASSWORD } });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { access_token: string }).access_token;
}

async function resetLesson(request: APIRequestContext, mode: "card_response" | "call_intake"): Promise<string> {
  const token = await apiToken(request, "student1");
  const headers = { Authorization: `Bearer ${token}` };
  const assignments = (await (await request.get("/api/me/assignments", { headers })).json()) as { id: string; status: string; mode: string }[];
  const running = assignments.find((a) => a.status === "running" && a.mode === mode);
  expect(running, `seed must provide a running ${mode} lesson for student1`).toBeTruthy();
  expect((await request.post(`/api/sessions/${running!.id}/restart`, { headers })).status()).toBe(204);
  return running!.id;
}

async function loginAsStudent(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "Войти как обучающийся" }).click();
  await expect(page.getByRole("heading", { name: "Мои задания" })).toBeVisible();
}

test.describe("Баги стенда", () => {
  test("BUGS 1: «Сохранить» без «Завершить» закрывает звонок, софтфон не виснет в «разговоре»", async ({ page, request }) => {
    test.setTimeout(240_000);
    await resetLesson(request, "call_intake");
    await loginAsStudent(page);
    await page.getByRole("link", { name: /Открыть АРМ оператора 112/ }).click();
    await expect(page).toHaveURL(/\/student\/attempts\/[0-9a-f-]+$/, { timeout: 15_000 });
    const panel = page.getByTestId("call-panel");
    await expect(panel).toContainText("входящий", { timeout: 15_000 });
    await page.getByTestId("call-answer").click();
    await expect(panel).toContainText("разговор", { timeout: 90_000 });
    const phrase = page.getByLabel("Фраза заявителю");
    await phrase.fill("Назовите адрес, пожалуйста");
    await phrase.press("Enter");
    const transcript = page.getByRole("list", { name: "Стенограмма разговора" });
    await expect(transcript.locator("li[data-role=caller]")).toHaveCount(2, { timeout: 90_000 });

    // Save straight away, without hanging up.
    await page.getByLabel("Фамилия и имя заявителя").fill("Петров Иван Сергеевич");
    await page.keyboard.press("Control+Enter");
    await expect(page.getByTestId("card-score")).toBeVisible({ timeout: 120_000 });

    // The call is over on the server, and the panel says so instead of staying in «разговор».
    await expect(panel).toContainText("завершён", { timeout: 15_000 });
    await expect(panel).toContainText("карточка сохранена");
    await expect(panel).not.toContainText("Карточка уже закрыта");
    // Closing the ended call leaves the softphone ready for the next one.
    await panel.getByRole("button", { name: "Закрыть" }).click();
    await expect(panel).toHaveAttribute("data-status", "ready");
    // «Следующий вызов» leads to the calls page, which rings the next call.
    await page.getByRole("button", { name: "Следующий вызов" }).click();
    await expect(page).toHaveURL(/\/student\/attempts\/[0-9a-f-]+$/, { timeout: 15_000 });
    await expect(panel).toContainText("входящий", { timeout: 15_000 });
  });

  test("BUGS 9: норматив второй карточки не идёт, пока обучающийся на разборе первой", async ({ page, request }) => {
    test.setTimeout(180_000);
    // A lesson of its own: one card at a time, two cards of the управа, so the second one is
    // added only when the trainee comes back to the journal.
    const teacher = await apiToken(request, "teacher1");
    const th = { Authorization: `Bearer ${teacher}` };
    const groups = (await (await request.get("/api/groups", { headers: th })).json()) as { id: string; title: string }[];
    const group = groups.find((g) => g.title === "Учебная-1");
    expect(group, "seed must provide «Учебная-1»").toBeTruthy();
    const created = await request.post("/api/sessions", {
      headers: th,
      data: {
        title: "BUGS 9: вторая карточка",
        group_id: group!.id,
        difficulty: 1,
        service_profile: ["territorial_oiv"],
        norm_seconds: 30,
        pass_threshold: 70,
        cards_per_student: 2,
      },
    });
    expect(created.status(), await created.text()).toBe(201);
    const sessionId = ((await created.json()) as { id: string }).id;
    expect((await request.post(`/api/sessions/${sessionId}/start`, { headers: th })).ok()).toBeTruthy();

    await loginAsStudent(page);
    await page.goto(`/student/sessions/${sessionId}/journal`);
    const cards = page.getByRole("link", { name: /Открыть карточку/ });
    await expect(cards).toHaveCount(1);
    await cards.first().click();
    await expect(page.getByTestId("own-service-panel")).toContainText("Получена службой");

    // Close the first card as it is, then stay on it longer than the norm.
    await page.getByRole("button", { name: "Завершить работу с карточкой" }).click();
    await page.getByRole("button", { name: "Да, завершить" }).click();
    await expect(page.getByTestId("card-score")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("button", { name: "В журнал" })).toBeVisible();
    await page.waitForTimeout(35_000);

    // Back in the journal the second card is added now: its timer is fresh, not overdue.
    await page.getByRole("button", { name: "В журнал" }).click();
    await expect(page).toHaveURL(new RegExp(`/student/sessions/${sessionId}/journal$`));
    await expect(cards).toHaveCount(2, { timeout: 15_000 });
    const active = cards.filter({ has: page.locator("[data-phase='ok'], [data-phase='warning']") });
    await expect(active).toHaveCount(1);
    await expect(active.locator("[data-phase]")).toHaveAttribute("aria-label", /До конца норматива 00:(1\d|2\d|30)/);

    await request.post(`/api/sessions/${sessionId}/finish`, { headers: th });
  });
});
