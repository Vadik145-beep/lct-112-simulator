import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Wave 7 acceptance ("Проверка" of plan/wave-07.md) against the compose stand, browser path
// of the softphone (TELEPHONY_ENABLED=false: microphone / text instead of Asterisk): the
// seeded call-intake lesson of student1, reset through the demo-only restart endpoint.
// Screenshots go to docs/screenshots/wave-07/. The SIP path is e2e/softphone.spec.ts.

const SHOTS = "../docs/screenshots/wave-07";
const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";

test.describe.configure({ mode: "serial" });

async function apiToken(request: APIRequestContext, login: string): Promise<string> {
  const res = await request.post("/api/auth/login", { data: { login, password: PASSWORD } });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { access_token: string }).access_token;
}

async function resetCallSession(request: APIRequestContext): Promise<{ sessionId: string; headers: Record<string, string> }> {
  const token = await apiToken(request, "student1");
  const headers = { Authorization: `Bearer ${token}` };
  const assignments = (await (await request.get("/api/me/assignments", { headers })).json()) as { id: string; status: string; mode: string }[];
  const running = assignments.find((a) => a.status === "running" && a.mode === "call_intake");
  expect(running, "seed must provide a running call-intake session for student1").toBeTruthy();
  const reset = await request.post(`/api/sessions/${running!.id}/restart`, { headers });
  expect(reset.status()).toBe(204);
  return { sessionId: running!.id, headers };
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
    // blob: URLs are the voiced replies fetched with the token and played from memory.
    if (!req.url().startsWith("blob:") && new URL(req.url()).host !== base.host) foreign.push(req.url());
  });
  return { problems, foreign };
}

async function loginAsStudent(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "Войти как обучающийся" }).click();
  await expect(page.getByRole("heading", { name: "Мои задания" })).toBeVisible();
}

async function pickSign(page: Page, title: string) {
  await page.getByTestId("survey-card").getByRole("button", { name: title, exact: true }).first().click();
}

test.describe("Волна 7: приём вызова от звонка до разбора", () => {
  test("вызов → ответ → вопрос про адрес → карточка → службы → сохранение → разбор", async ({ page, request }) => {
    test.setTimeout(240_000);
    const { sessionId } = await resetCallSession(request);
    const { problems, foreign } = watchNetwork(page);

    await loginAsStudent(page);
    await page.getByRole("link", { name: /Открыть АРМ оператора 112/ }).click();
    // The workplace asks for a call and opens the card as soon as one rings.
    await expect(page).toHaveURL(/\/student\/attempts\/[0-9a-f-]+$/, { timeout: 15_000 });
    const attemptId = page.url().split("/").pop()!;
    // The softphone (browser mode) picks the ringing call up from /me/call.
    const panel = page.getByTestId("call-panel");
    await expect(panel).toContainText("входящий", { timeout: 15_000 });
    await expect(page.getByTestId("aon")).not.toHaveText("");
    await page.screenshot({ path: `${SHOTS}/01-ringing.png`, fullPage: true });

    // Answer: the conversation timer starts, the caller can be talked to (text: no STT here).
    await page.getByTestId("call-answer").click();
    // Answering voices the opening line: on a cold stand Piper loads the voice first.
    await expect(panel).toContainText("разговор", { timeout: 90_000 });
    const phrase = page.getByLabel("Фраза заявителю");
    await phrase.fill("Назовите адрес, пожалуйста");
    await phrase.press("Enter");
    const transcript = page.getByRole("list", { name: "Стенограмма разговора" });
    // The first reply of a cold stand loads the Piper voice from the bind mount (tens of seconds).
    await expect(transcript.locator("li[data-role=caller]")).toHaveCount(2, { timeout: 90_000 });
    await expect(page.getByTestId("topics-hint").locator("li[data-covered=true]")).toHaveCount(1);
    await expect(page.getByTestId("talk-timer")).not.toHaveText("00:00");

    // The card: the seed puts the gas-pipe call first (difficulty 1).
    await page.getByLabel("Фамилия и имя заявителя").fill("Петров Иван Сергеевич");
    await page.getByLabel("Статус заявителя").selectOption("жилец");
    const street = page.getByRole("combobox", { name: "Улица" });
    await street.fill("Вавил");
    // The street runs through several districts: pick the ЮЗАО row (the reference).
    await page.getByRole("listbox", { name: "Подсказка улиц" }).getByRole("button", { name: /ЮЗАО, Ломоносовский/ }).click();
    await expect(street).toHaveValue("улица Вавилова");
    await expect(page.getByLabel("Округ")).toHaveValue("ЮЗАО");
    await expect(page.getByLabel("Район")).not.toHaveValue("");
    await page.getByLabel("Дом/Вл.").fill("81");
    await page.getByLabel("Корпус").fill("1");
    await page.getByLabel("Квартира/офис").fill("5");
    await page.getByLabel("Подъезд").fill("1");
    await page.getByLabel("Этаж").fill("2");
    await page.getByLabel("Код").fill("5В");
    await page.getByLabel("Описание со слов заявителя").fill("Свист от газовой трубы в квартире, запах газа. 03 не требуется.");
    await expect(page.getByTestId("description-counter")).toContainText("/ 1999");

    // Survey card: group → sign → sign; the services strip fills itself.
    await page.getByRole("button", { name: "добавить тип происшествия" }).click();
    await page.getByRole("listbox", { name: "Группа происшествия" }).getByRole("button", { name: /^13\./ }).click();
    await pickSign(page, "Запах газа в помещении");
    await pickSign(page, "От газововго оборудования"); // sic: the classifier's own spelling
    await expect(page.getByTestId("survey-type")).toContainText(/газ/i);
    const strip = page.getByTestId("services-strip");
    await expect(strip.locator("[data-service='101']")).toBeVisible({ timeout: 10_000 });
    await expect(strip.locator("[data-service='mosgaz']")).toBeVisible();
    // A service added by hand stays after the type resolves again.
    await page.getByRole("button", { name: "Добавить службу" }).click();
    await page.getByLabel("Служба для добавления").selectOption("103");
    await expect(strip.locator("[data-service='103']")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/02-card-filled.png`, fullPage: true });

    // A reload in the middle of the call keeps the draft (localStorage + server).
    await page.waitForTimeout(2500);
    await page.reload();
    await expect(page.getByLabel("Фамилия и имя заявителя")).toHaveValue("Петров Иван Сергеевич");
    await expect(page.getByLabel("Дом/Вл.")).toHaveValue("81");
    await expect(page.getByTestId("survey-type")).toContainText(/газ/i);
    await expect(strip.locator("[data-service='103']")).toBeVisible();
    await expect(transcript.locator("li[data-role=caller]")).toHaveCount(2);

    // Save (Ctrl+Enter): scored at once, the next call is offered.
    await page.keyboard.press("Control+Enter");
    // The first evaluation of a cold stand loads the e5 model (tens of seconds on a bind mount).
    await expect(page.getByTestId("card-score")).toBeVisible({ timeout: 120_000 });
    const score = Number(await page.getByTestId("card-score").textContent());
    expect(score).toBeGreaterThanOrEqual(50);
    await expect(page.getByRole("button", { name: "Следующий вызов" })).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/03-saved.png`, fullPage: true });

    // Review: components, topics, transcript, address against the reference.
    await page.getByRole("link", { name: "Открыть разбор" }).click();
    await expect(page).toHaveURL(new RegExp(`/student/attempts/${attemptId}/review$`));
    await expect(page.getByTestId("review-total")).toHaveText(String(score));
    await expect(page.getByRole("progressbar", { name: "Опросная карта" })).toBeVisible();
    await expect(page.getByRole("list", { name: "Темы" }).locator("li[data-topic=address][data-covered=true]")).toBeVisible();
    await expect(page.getByRole("list", { name: "Реплики" }).locator("li")).toHaveCount(3);
    await expect(page.getByTestId("review-services")).toContainText(/Мосгаз|mosgaz/);
    await page.screenshot({ path: `${SHOTS}/04-review.png`, fullPage: true });

    // The next call rings on the workplace page.
    await page.goto(`/student/sessions/${sessionId}/calls`);
    await expect(page).toHaveURL(/\/student\/attempts\/[0-9a-f-]+$/, { timeout: 15_000 });
    await expect(page.getByTestId("call-panel")).toContainText("входящий", { timeout: 15_000 });

    expect(problems, problems.join("\n")).toEqual([]);
    expect(foreign, foreign.join("\n")).toEqual([]);
  });

  test("вызов из другого региона: карточка без региона → детектор region_not_clarified", async ({ page, request }) => {
    test.setTimeout(240_000);
    const { headers } = await resetCallSession(request);

    // Walk the queue by API until the Volzhsky call (difficulty 3) rings.
    const findVolzhsky = async (): Promise<string> => {
      for (let i = 0; i < 12; i += 1) {
        const assignments = (await (await request.get("/api/me/assignments", { headers })).json()) as { id: string; mode: string; status: string }[];
        const session = assignments.find((a) => a.status === "running" && a.mode === "call_intake")!;
        const journal = (await (await request.get(`/api/sessions/${session.id}/journal`, { headers })).json()) as {
          items: { attempt_id: string; state: string }[];
        };
        const active = journal.items.find((it) => ["issued", "received", "in_progress"].includes(it.state));
        expect(active, "queue exhausted before the Volzhsky call").toBeTruthy();
        const attempt = (await (await request.get(`/api/attempts/${active!.attempt_id}`, { headers })).json()) as {
          intake: { required_topics: string[] };
        };
        if (attempt.intake.required_topics.includes("region")) return active!.attempt_id;
        const saved = await request.post(`/api/attempts/${active!.attempt_id}/submit`, {
          headers,
          data: { card: { description: "пропуск" }, client_submission_id: `skip-${i}-${Date.now()}` },
        });
        expect(saved.ok()).toBeTruthy();
      }
      throw new Error("no region call in the queue");
    };
    const attemptId = await findVolzhsky();

    await loginAsStudent(page);
    await page.goto(`/student/attempts/${attemptId}`);
    await page.getByTestId("call-answer").click({ timeout: 15_000 });
    const phrase = page.getByLabel("Фраза заявителю");
    await phrase.fill("Где вы находитесь, адрес?");
    await phrase.press("Enter");
    await expect(page.getByRole("list", { name: "Стенограмма разговора" }).locator("li[data-role=caller]")).toHaveCount(2, { timeout: 90_000 });
    // The operator fills Moscow-style: the street only, no region.
    await page.getByRole("combobox", { name: "Улица" }).fill("улица Карла Маркса");
    await page.keyboard.press("Escape");
    await page.getByLabel("Описание со слов заявителя").fill("Ребёнок 11 лет упал с велосипеда, отёк руки.");
    await page.getByTestId("save-card").click();
    await expect(page.getByTestId("card-score")).toBeVisible({ timeout: 120_000 });
    await page.getByRole("link", { name: "Открыть разбор" }).click();
    await expect(page.locator("[data-error=region_not_clarified]")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/05-region-not-clarified.png`, fullPage: true });
  });
});
