import { expect, test, type APIRequestContext } from "@playwright/test";

// ТЗ «Настройка учебной среды» on the compose stand: the teacher picked a category no seed
// scenario covers (24, БПЛА), the lesson page names the group, drafts scenarios under it,
// and an approved draft enters the queue. Generation runs by the template when no model is
// configured (LLM_GEN_URL empty), so the job ends in seconds.

const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";

async function apiToken(request: APIRequestContext, login: string): Promise<string> {
  const res = await request.post("/api/auth/login", { data: { login, password: PASSWORD } });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { access_token: string }).access_token;
}

test("занятие без сценариев: группа названа словами, генерация под неё, утверждённый черновик входит в очередь", async ({ page, request }) => {
  test.setTimeout(180_000);
  const token = await apiToken(request, "teacher1");
  const headers = { Authorization: `Bearer ${token}` };
  const groups = (await (await request.get("/api/groups", { headers })).json()) as { id: string }[];
  expect(groups.length).toBeGreaterThan(0);
  const created = await request.post("/api/sessions", {
    headers,
    data: {
      title: `Генерация под БПЛА ${new Date().toLocaleTimeString("ru-RU")}`,
      mode: "call_intake",
      group_id: groups[0].id,
      incident_groups: ["24"],
      difficulty: 1,
      service_profile: [],
      norm_seconds: 90,
      pass_threshold: 70,
    },
  });
  expect(created.status()).toBe(201);
  const lesson = (await created.json()) as { id: string; queue: unknown[] };
  expect(lesson.queue).toEqual([]);

  await page.goto("/login");
  await page.getByLabel("Логин").fill("teacher1");
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  await page.goto(`/teacher/sessions/${lesson.id}`);

  // The code alone read as «24 groups» on the stand; the title must be there.
  await expect(page.getByTestId("session-groups")).toContainText("24 — БПЛА");
  await expect(page.getByText("нет утверждённых сценариев")).toBeVisible();

  const block = page.getByTestId("generate-for-lesson");
  await block.getByLabel("Сколько").fill("1");
  await block.getByRole("button", { name: "Сгенерировать" }).click();
  const list = page.getByTestId("generated-list");
  await expect(list).toBeVisible({ timeout: 120_000 });
  await expect(list).toContainText("Готово: 1 сценарий");
  const link = list.getByRole("link").first();
  const scenarioUrl = await link.getAttribute("href");
  expect(scenarioUrl).toMatch(/\/teacher\/scenarios\//);

  // The draft is «на проверке» and about drones; approved, it enters the queue.
  const scenarioId = scenarioUrl!.split("/").pop()!;
  const scenario = (await (await request.get(`/api/scenarios/${scenarioId}`, { headers })).json()) as {
    status: string;
    incident_type_code: string;
  };
  expect(scenario.status).toBe("review");
  expect(scenario.incident_type_code.startsWith("24.")).toBeTruthy();
  const approved = await request.post(`/api/scenarios/${scenarioId}/approve`, {
    headers,
    data: { reference: true, replies: true, confirm_grammar: true },
  });
  expect(approved.status()).toBe(200);

  await page.reload();
  await expect(page.getByText("Очередь карточек · 1")).toBeVisible();
  await expect(page.getByText("нет утверждённых сценариев")).toBeHidden();
});
