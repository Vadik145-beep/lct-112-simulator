import { expect, test, type Page } from "@playwright/test";

// Wave 0 acceptance ("Показать" section of plan/wave-00.md), run against the compose stand.
// Screenshots go to docs/screenshots/wave-00/.

const SHOTS = "../docs/screenshots/wave-00";

const ROLES = [
  {
    role: "student",
    button: "Войти как обучающийся",
    title: "Мои задания",
    name: "Кузнецов Обучающийся 1",
    label: "Обучающийся",
    home: "/student",
    foreign: "/teacher",
  },
  {
    role: "teacher",
    button: "Войти как преподаватель",
    title: "Занятия",
    name: "Иванова Мария Петровна",
    label: "Преподаватель",
    home: "/teacher",
    foreign: "/admin",
  },
  {
    role: "admin",
    button: "Войти как администратор",
    title: "Кабинет администратора",
    name: "Администратор системы",
    label: "Администратор",
    home: "/admin",
    foreign: "/student",
  },
] as const;

/** Collects console errors and failed requests so a test can assert the console stayed clean. */
function watchConsole(page: Page): string[] {
  const problems: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") problems.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (err) => problems.push(`pageerror: ${err.message}`));
  page.on("requestfailed", (req) => problems.push(`request failed: ${req.url()}`));
  return problems;
}

test.describe("Волна 0: вход, роли, кабинеты", () => {
  test("страница входа открывается без ошибок в консоли, демо-кнопки видны", async ({ page }) => {
    const problems = watchConsole(page);
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { name: "Тренажёр оператора ДДС" })).toBeVisible();
    for (const r of ROLES) await expect(page.getByRole("button", { name: r.button })).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/01-login.png`, fullPage: true });
    expect(problems).toEqual([]);
  });

  for (const r of ROLES) {
    test(`демо-вход: ${r.label}, свой кабинет, чужой закрыт, выход`, async ({ page }) => {
      const problems = watchConsole(page);
      await page.goto("/login");
      await page.getByRole("button", { name: r.button }).click();

      await expect(page).toHaveURL(new RegExp(`${r.home}$`));
      await expect(page.getByRole("heading", { name: r.title })).toBeVisible();
      await expect(page.getByText(r.name)).toBeVisible();
      await expect(page.getByText(r.label, { exact: true })).toBeVisible();
      await page.screenshot({ path: `${SHOTS}/02-${r.role}.png`, fullPage: true });

      // Someone else's cabinet by direct URL: the page says "Нет доступа" and stays there.
      await page.goto(r.foreign);
      await expect(page.getByRole("heading", { name: "Нет доступа" })).toBeVisible();
      if (r.role === "student") await page.screenshot({ path: `${SHOTS}/03-forbidden.png`, fullPage: true });
      await page.getByRole("link", { name: "Перейти в свой кабинет" }).click();
      await expect(page).toHaveURL(new RegExp(`${r.home}$`));

      // Session survives a reload.
      await page.reload();
      await expect(page.getByRole("heading", { name: r.title })).toBeVisible();

      await page.getByRole("button", { name: "Выйти" }).click();
      await expect(page).toHaveURL(/\/login$/);
      // After logout the protected page is closed again.
      await page.goto(r.home);
      await expect(page).toHaveURL(/\/login$/);

      // The API itself refuses the foreign cabinet (403), not only the interface.
      expect(problems.filter((p) => !p.includes("/api/") || !p.includes("403"))).toEqual([]);
    });
  }

  test("после входа обучающимся сразу вход преподавателем ведёт в кабинет преподавателя", async ({ page }) => {
    // Regression: a stale return path (/student) must not send a teacher to "Нет доступа".
    await page.goto("/login");
    await page.getByRole("button", { name: "Войти как обучающийся" }).click();
    await expect(page).toHaveURL(/\/student$/);
    await page.getByRole("button", { name: "Выйти" }).click();
    await page.getByRole("button", { name: "Войти как преподаватель" }).click();
    await expect(page).toHaveURL(/\/teacher$/);
    await expect(page.getByRole("heading", { name: "Занятия" })).toBeVisible();
  });

  test("вход по логину и паролю, неверный пароль показывает понятную ошибку", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Логин").fill("teacher2");
    await page.getByLabel("Пароль").fill("wrong-password");
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveText("Неверный логин или пароль.");

    await page.getByLabel("Пароль").fill("Demo12345");
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Занятия" })).toBeVisible();
    await expect(page.getByText("Сидоров Алексей Николаевич")).toBeVisible();
  });

  test("переключатель темы меняет тему и запоминает её", async ({ page }) => {
    await page.goto("/login");
    const html = page.locator("html");
    const wasDark = await html.evaluate((el) => el.classList.contains("dark"));
    await page.getByRole("button", { name: wasDark ? "Включить светлую тему" : "Включить тёмную тему" }).click();
    await expect(html).toHaveClass(wasDark ? /^(?!.*dark)/ : /dark/);
    await page.screenshot({ path: `${SHOTS}/04-theme-${wasDark ? "light" : "dark"}.png`, fullPage: true });
    await page.reload();
    expect(await html.evaluate((el) => el.classList.contains("dark"))).toBe(!wasDark);
  });

  test("страница не ходит во внешний интернет", async ({ page }) => {
    const external: string[] = [];
    page.on("request", (req) => {
      const host = new URL(req.url()).host;
      if (!/^(localhost|127\.0\.0\.1)(:\d+)?$/.test(host)) external.push(req.url());
    });
    await page.goto("/login");
    await page.getByRole("button", { name: "Войти как администратор" }).click();
    await expect(page.getByRole("heading", { name: "Кабинет администратора" })).toBeVisible();
    expect(external).toEqual([]);
  });
});
