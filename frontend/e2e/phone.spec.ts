import { expect, test, type APIRequestContext } from "@playwright/test";

// Задача #263: виртуальный телефон ДДС. Преподаватель видит справочник телефонов; обучающийся
// открывает телефон зелёным кружком, набирает несуществующий номер, затем начальника аварийной
// бригады из справочника; на занятии сложности 2 тот путает дом, диспетчер поправляет и даёт
// распоряжение на выезд; звонок заявителю по трубке «АОН» проходит, потому что занятие его
// разрешает. Прогон против поднятого контура, как и остальные e2e.

const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";
const CARD_NUMBER = "38260421"; // «Отключение отопления в нескольких домах», МОЭК
const ADDRESS_AS_IN_CARD =
  "Авария, адрес: Россия, Москва, (ВАО, район Ивановское), улица Молостовых, 10, к. 1. " +
  "Нет отопления в трёх домах, пострадавших нет.";

test.describe.configure({ mode: "serial" });

async function apiToken(
  request: APIRequestContext,
  login: string,
): Promise<string> {
  const res = await request.post("/api/auth/login", {
    data: { login, password: PASSWORD },
  });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { access_token: string }).access_token;
}

test.describe("Задача #263: виртуальный телефон", () => {
  test("справочник у преподавателя → кружок → неверный номер → начальник бригады путает дом → распоряжение → звонок заявителю", async ({
    page,
    request,
  }) => {
    test.setTimeout(180_000);
    const teacher = await apiToken(request, "teacher1");
    const th = { Authorization: `Bearer ${teacher}` };
    const stamp = new Date().toLocaleTimeString("ru-RU");

    // The chief of the МОЭК squad in the directory the teacher keeps.
    const directory = (await (
      await request.get("/api/phone-directory", { headers: th })
    ).json()) as {
      entries: { service_code: string; role: string; number: string }[];
    };
    const chief = directory.entries.find(
      (e) => e.service_code === "moek" && e.role === "squad_chief",
    );
    expect(
      chief,
      "у МОЭК в справочнике есть начальник аварийной бригады",
    ).toBeDefined();

    // A lesson of difficulty 2 with the call to the caller allowed, for student3 alone.
    const students = (await (
      await request.get("/api/students", { headers: th })
    ).json()) as {
      id: string;
      login: string;
    }[];
    const group = await request.post("/api/groups", {
      headers: th,
      data: {
        title: `Группа #263 · ${stamp}`,
        student_ids: students
          .filter((s) => s.login === "student3")
          .map((s) => s.id),
      },
    });
    expect(group.status(), await group.text()).toBe(201);
    const scenarios = (await (
      await request.get("/api/scenarios", {
        headers: th,
        params: {
          kind: "card_response",
          q: "Отключение отопления в нескольких домах",
        },
      })
    ).json()) as { items: { id: string }[] };
    expect(scenarios.items.length, "в сиде есть карточка МОЭК").toBeGreaterThan(
      0,
    );
    const created = await request.post("/api/sessions", {
      headers: th,
      data: {
        title: `#263: телефон · ${stamp}`,
        group_id: ((await group.json()) as { id: string }).id,
        difficulty: 2,
        scenario_ids: [scenarios.items[0]!.id],
        service_profile: ["moek"],
        dialog_mode: "buttons",
        allow_caller_call: true,
      },
    });
    expect(created.status(), await created.text()).toBe(201);
    const sessionId = ((await created.json()) as { id: string }).id;
    expect(
      (
        await request.post(`/api/sessions/${sessionId}/start`, { headers: th })
      ).ok(),
    ).toBeTruthy();

    // The teacher's page: the service's numbers, the chief among them.
    await page.goto("/login");
    await page.getByLabel("Логин").fill("teacher1");
    await page.getByLabel("Пароль").fill(PASSWORD);
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    // Signed in once the page leaves the login screen.
    await expect(page).not.toHaveURL(/\/login/);
    await page.getByRole("link", { name: "Справочник телефонов" }).click();
    await expect(
      page.getByRole("heading", { name: "Справочник телефонов" }),
    ).toBeVisible();
    await page.getByLabel("Служба").selectOption({ label: "МОЭК" });
    await expect(
      page.locator(`tr[data-number="${chief!.number}"]`),
    ).toBeVisible();
    await page.context().clearCookies();
    await page.evaluate(() => {
      localStorage.clear();
      sessionStorage.clear();
    });

    // The trainee opens the card and the phone.
    await page.goto("/login");
    await page.getByLabel("Логин").fill("student3");
    await page.getByLabel("Пароль").fill(PASSWORD);
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    // Signed in once the page leaves the login screen.
    await expect(page).not.toHaveURL(/\/login/);
    await page.goto(`/student/sessions/${sessionId}/journal`);
    await page
      .getByRole("link", {
        name: new RegExp(`Открыть карточку ${CARD_NUMBER}`),
      })
      .click();
    await expect(page.getByText(`Происшествие ${CARD_NUMBER}`)).toBeVisible();

    await page.getByTestId("virtual-phone-button").click();
    const phone = page.getByTestId("virtual-phone");
    await expect(phone).toBeVisible();

    // A number nobody has.
    for (const key of ["0", "0", "0", "0"])
      await phone.getByRole("button", { name: key, exact: true }).click();
    await phone.getByRole("button", { name: /Вызов/ }).click();
    await expect(phone.getByRole("alert")).toHaveText(
      "Набранный номер не существует.",
    );

    // The chief from the book: the number lands in the field, the call goes through.
    await phone
      .getByRole("button", { name: /Начальник аварийной бригады/ })
      .click();
    await expect(phone.getByLabel("Набираемый номер")).toHaveValue(
      chief!.number,
    );
    await phone.getByRole("button", { name: /Вызов/ }).click();
    const call = phone.getByTestId("service-call-panel");
    await expect(call).toHaveAttribute("data-state", "talking");
    await expect(call.locator("li[data-role=officer]").first()).toContainText(
      "Начальник аварийной бригады МОЭК, слушаю.",
    );

    const say = async (text: string) => {
      const before = await call.locator("li[data-role=officer]").count();
      await call.getByLabel("Сказать: начальник аварийной бригады").fill(text);
      await call
        .getByRole("button", { name: "Отправить: начальник аварийной бригады" })
        .click();
      await expect(call.locator("li[data-role=officer]")).toHaveCount(
        before + 1,
        { timeout: 30_000 },
      );
      return call.locator("li[data-role=officer]").last();
    };
    // Difficulty 2: the address as the card writes it is repeated with a wrong house.
    await expect(await say(ADDRESS_AS_IN_CARD)).toContainText(
      "Записал: улица Молостовых, дом 12, корпус 1?",
    );
    await expect(await say("Нет, дом 10.")).toContainText("Понял, дом 10.");
    // No squad number without the order; with it, the squad goes.
    await expect(await say("Высылайте бригаду на выезд.")).toContainText(
      /Принял, выезжаем\. Наряд \d+\./,
    );
    await call.getByRole("button", { name: "Завершить звонок" }).click();
    await expect(call).toHaveAttribute("data-state", "ended");

    // The caller of the card: the lesson allows it, so the handset of «АОН» rings him.
    await page
      .getByRole("button", { name: /Позвонить заявителю: АОН/ })
      .click();
    const callerCall = page.locator(
      '[data-testid="service-call-panel"][data-kind="caller"]',
    );
    await expect(callerCall).toHaveAttribute("data-state", "talking");
    await callerCall.getByRole("button", { name: "Завершить звонок" }).click();
    await expect(callerCall).toHaveAttribute("data-state", "ended");
  });
});
