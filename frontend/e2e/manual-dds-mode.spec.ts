import { expect, test, type Page } from "@playwright/test";

/**
 * Ручной прогон выбора режима в занятии ДДС: преподаватель заводит занятие через форму,
 * выбирает «Кнопки тем» и выключает голос, обучающийся звонит дежурному службы и
 * разговаривает текстом. Снимки — в ../docs/screenshots/manual-dds-mode.
 */

const PASSWORD = process.env.PROBE_PASSWORD ?? "Probe12345";
const SHOTS = "../docs/screenshots/manual-dds-mode";
const stamp = new Date().toLocaleTimeString("ru-RU");

async function signIn(page: Page, login: string, heading: RegExp) {
  // если в браузере уже кто-то сидит — выходим кнопкой, как это делает человек
  const logout = page.getByRole("button", { name: "Выйти" });
  if (await logout.isVisible().catch(() => false)) {
    await logout.click();
    await expect(page.getByLabel("Логин")).toBeVisible({ timeout: 30_000 });
  }
  await page.goto("/login");
  await expect(page.getByLabel("Логин")).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Логин").fill(login);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.getByRole("heading", { name: heading })).toBeVisible({ timeout: 30_000 });
}

test("преподаватель выбирает режим для ДДС, обучающийся звонит дежурному", async ({
  page,
  request,
}) => {
  test.setTimeout(420_000);

  // --- преподаватель заводит занятие руками, через форму
  await signIn(page, "probe_teacher", /Занятия|Кабинет преподавателя/);
  await page.goto("/teacher/sessions/new");
  await expect(page.getByRole("heading", { name: "Новое занятие" })).toBeVisible();
  await page.getByLabel("Название").fill(`Режим ДДС · ${stamp}`);

  // режим занятия — реагирование на карточку (он и так первый, но нажмём как человек)
  await page.getByRole("radio", { name: /Реагирование на карточку/ }).check();
  const modeSelect = page.getByLabel("Как отвечают служба и бригада");
  await expect(modeSelect).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/01-форма-занятия.png`, fullPage: true });

  // облачного голоса тут быть не должно
  const options = await modeSelect.locator("option").allInnerTexts();
  console.log("режимы для ДДС:", options.join(" | "));
  expect(options.join(" ")).not.toContain("Облачный голос");
  await expect(page.getByTestId("dds-mode-note")).toContainText("Облачный голос сюда пока");

  await modeSelect.selectOption("buttons");
  const voice = page.getByRole("checkbox", { name: /Голос: служба и бригада/ });
  await expect(voice).toBeChecked();
  await voice.uncheck();

  await page.getByLabel("Группа").selectOption({ index: 0 });
  await page.getByRole("button", { name: /Создать занятие|Сохранить/ }).first().click();
  await expect(page.getByRole("heading", { name: new RegExp(`Режим ДДС`) })).toBeVisible({
    timeout: 30_000,
  });
  await page.screenshot({ path: `${SHOTS}/02-занятие-создано.png`, fullPage: true });
  const sessionId = page.url().split("/").pop()!;

  await page.getByRole("button", { name: /Начать занятие/ }).click();
  await expect(page.getByText(/идёт|Завершить занятие/).first()).toBeVisible({ timeout: 30_000 });

  // --- обучающийся: звонок дежурному службы текстом
  await signIn(page, "probe_student", /Мои задания/);
  await page.goto(`/student/sessions/${sessionId}/journal`);
  await page.getByRole("link", { name: /Открыть карточку/ }).first().click();
  await expect(page.getByTestId("own-service-panel")).toBeVisible();

  await page.getByRole("button", { name: /Позвонить/ }).first().click();
  const call = page.locator('[data-testid="service-call-panel"][data-kind="outgoing"]');
  await expect(call).toBeVisible({ timeout: 30_000 });
  await expect(call).toContainText("слушаю");
  console.log(
    "дежурный:",
    (await call.locator("li[data-role='officer']").first().innerText()).trim(),
  );
  await page.screenshot({ path: `${SHOTS}/03-звонок-дежурному.png`, fullPage: true });

  // голос выключен: запись не прикладывается, значит и не проигрывается
  const audio = await call.locator("audio").count();
  expect(audio).toBe(0);

  const field = call.getByRole("textbox").first();
  await field.fill("Улица Молостовых, дом 10, корпус 1, нет отопления, пострадавших нет, наряд 4127");
  await field.press("Enter");
  await expect(call.locator("li[data-role='officer']")).toHaveCount(2, { timeout: 30_000 });
  console.log(
    "ответ дежурного:",
    (await call.locator("li[data-role='officer']").last().innerText()).trim(),
  );
  await page.screenshot({ path: `${SHOTS}/04-ответ-дежурного.png`, fullPage: true });

  await call.getByRole("button", { name: "Завершить звонок" }).click();
  // Панель остаётся в карточке как история звонка, но уже завершённого.
  await expect(call).toHaveAttribute("data-state", "ended", { timeout: 30_000 });
  await expect(call).toContainText("завершён");

  // прибираем за собой
  const token = await (async () => {
    const r = await request.post("/api/auth/login", {
      data: { login: "probe_teacher", password: PASSWORD },
    });
    return ((await r.json()) as { access_token: string }).access_token;
  })();
  await request.post(`/api/sessions/${sessionId}/finish`, {
    headers: { Authorization: `Bearer ${token}` },
  });
});
