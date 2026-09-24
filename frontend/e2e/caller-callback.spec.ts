import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Обратный звонок диспетчера заявителю (ответ заказчика 23.09.2026): трубка у телефона
// карточки, разговор с заявителем, студийная запись его голоса. Прогон против поднятого
// контура, как и остальные e2e; скриншоты кладутся в docs/screenshots/caller-callback/.

const SHOTS = "../docs/screenshots/caller-callback";
const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";

test.describe.configure({ mode: "serial" });

async function apiToken(request: APIRequestContext, login: string): Promise<string> {
  const res = await request.post("/api/auth/login", {
    data: { login, password: PASSWORD },
  });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { access_token: string }).access_token;
}

/** Возвращает занятие ДДС в исходное состояние, чтобы прогон повторялся. */
async function resetDemoSession(request: APIRequestContext): Promise<string> {
  const token = await apiToken(request, "student1");
  const headers = { Authorization: `Bearer ${token}` };
  const assignments = (await (
    await request.get("/api/me/assignments", { headers })
  ).json()) as { id: string; status: string; mode: string }[];
  const running = assignments.find(
    (a) => a.status === "running" && a.mode === "card_response",
  );
  expect(running, "в сиде должно быть запущенное занятие ДДС").toBeTruthy();
  const reset = await request.post(`/api/sessions/${running!.id}/restart`, { headers });
  expect(reset.status()).toBe(204);
  return running!.id;
}

async function loginAsStudent(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "Войти как обучающийся" }).click();
  await expect(page.getByRole("heading", { name: "Мои задания" })).toBeVisible();
}

test.describe("Обратный звонок заявителю", () => {
  test("диспетчер жмёт трубку, говорит с заявителем и слышит его голос", async ({
    page,
    request,
  }) => {
    test.setTimeout(180_000);
    const sessionId = await resetDemoSession(request);

    // Записи заявителя, которые страница успела запросить у сервера.
    const played: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/api/media/tts/seed/_officers/_caller/")) played.push(req.url());
    });

    await loginAsStudent(page);
    await page.getByRole("link", { name: /Открыть журнал АРМ-112/ }).click();
    await expect(page).toHaveURL(new RegExp(`/student/sessions/${sessionId}/journal$`));

    await page.getByRole("link", { name: /Открыть карточку/ }).first().click();
    await expect(page.getByText(/Происшествие \d+/).first()).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/01-card.png`, fullPage: true });

    // Трубка у АОН: номер в карточке есть, значит кнопка активна.
    const dial = page.getByRole("button", { name: /Позвонить заявителю: АОН/ });
    await expect(dial).toBeEnabled();
    await dial.click();

    // Панель разговора: собеседник — заявитель, а не дежурный службы.
    const panel = page.getByTestId("service-call-panel");
    await expect(panel).toHaveAttribute("data-kind", "caller");
    await expect(panel).toContainText("Заявитель:");
    await expect(panel).toContainText("Алло");
    await page.screenshot({ path: `${SHOTS}/02-call-started.png`, fullPage: true });

    // Пока идёт разговор, второй звонок начать нельзя.
    await expect(dial).toBeDisabled();

    // Диспетчер спрашивает адрес — заявитель отвечает своим адресом из карточки.
    const field = panel.getByRole("textbox", { name: "Сказать: заявитель" });
    await field.fill("Назовите адрес, куда вызывали.");
    await field.press("Enter");
    await expect(panel.locator("li[data-role='officer']")).toHaveCount(2, {
      timeout: 30_000,
    });
    await expect(panel).toContainText(/улица|проспект|переулок|шоссе|набережная/i);

    // Второй вопрос: приехали ли.
    await field.fill("Помощь к вам уже приехала?");
    await field.press("Enter");
    await expect(panel.locator("li[data-role='officer']")).toHaveCount(3, {
      timeout: 30_000,
    });
    // Бригада ещё не выезжала, значит заявитель говорит, что никого нет.
    await expect(panel).toContainText(/никого нет|не приеха/i);
    await page.screenshot({ path: `${SHOTS}/03-talking.png`, fullPage: true });

    // Голос заявителя — студийная запись из банка, а не синтез на лету.
    expect(played.length, "страница должна запросить записи заявителя").toBeGreaterThan(0);

    await panel.getByRole("button", { name: /Завершить/ }).click();
    await expect(panel).toHaveAttribute("data-state", "ended");
    await expect(page.getByText(/Звонок заявителю/)).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/04-ended.png`, fullPage: true });

    // После разговора трубка снова активна: позвонить можно ещё раз.
    await expect(dial).toBeEnabled();
  });

  test("без номера в карточке трубка не звонит", async ({ page, request }) => {
    test.setTimeout(120_000);
    await resetDemoSession(request);
    await loginAsStudent(page);
    await page.getByRole("link", { name: /Открыть журнал АРМ-112/ }).click();
    await page.getByRole("link", { name: /Открыть карточку/ }).first().click();

    // «Телефон на месте» в сид-карточках пустой — по нему звонить некуда.
    const onSite = page.getByRole("button", { name: /Позвонить заявителю: телефон на месте/ });
    await expect(onSite).toBeDisabled();
    await expect(onSite).toHaveAttribute("title", /Номера нет/);
  });
});
