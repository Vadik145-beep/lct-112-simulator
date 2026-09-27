import { expect, test, type APIRequestContext } from "@playwright/test";

// The teacher picked a category no seed scenario covers (24, БПЛА): the lesson page names the
// group in words and says there is nothing to issue. Generation is no longer offered on this
// page (scenarios are generated in «Сценарии»).

const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";

async function apiToken(request: APIRequestContext, login: string): Promise<string> {
  const res = await request.post("/api/auth/login", { data: { login, password: PASSWORD } });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { access_token: string }).access_token;
}

test("занятие без сценариев: группа названа словами, предупреждение о пустом списке", async ({ page, request }) => {
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
  await expect(page.getByTestId("session-groups")).toContainText("БПЛА");
  await expect(page.getByText("нет утверждённых сценариев")).toBeVisible();
  await expect(page.getByTestId("generate-for-lesson")).toHaveCount(0);
});
