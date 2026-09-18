import { expect, test, type Page } from "@playwright/test";

// Wave 10 acceptance (plan/wave-10.md, «Показать»): the teacher opens the analytics of
// «Учебная-1» on the seed history, reads the forecast with its reasons and the model card;
// the trainee sees «Мой прогресс». Screenshots go to docs/screenshots/wave-10/.

const SHOTS = "../docs/screenshots/wave-10";
const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";

test.describe.configure({ mode: "serial" });

async function loginWithForm(page: Page, login: string) {
  await page.goto("/login");
  await page.getByLabel("Логин").fill(login);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page).toHaveURL(/\/(teacher|student|admin)/, { timeout: 15000 });
}

function watchErrors(page: Page): string[] {
  const problems: string[] = [];
  page.on("pageerror", (err) => problems.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") problems.push(`console: ${msg.text()}`);
  });
  return problems;
}

test("teacher: group analytics, forecast and the model card", async ({ page }) => {
  const problems = watchErrors(page);
  await loginWithForm(page, "teacher1");
  await page.getByRole("link", { name: "Аналитика" }).click();
  await expect(page).toHaveURL(/\/teacher\/analytics/);

  const volume = page.getByTestId("analytics-volume");
  await expect(volume).toContainText("Учебная-1");
  await expect(volume).toContainText("демонстрационные данные");
  await expect(page.getByTestId("analytics-summary")).toContainText("оценённых попыток");

  // Both heat maps and both dynamics charts are drawn (ECharts canvases).
  await expect(page.getByTestId("heatmap-card_response").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("heatmap-call_intake").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("dynamics-score").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("dynamics-time").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("top-errors").locator("li")).toHaveCount(5);

  // Six trainees, each with a probability, a risk badge and two reasons.
  const rows = page.getByTestId("readiness-table").locator("tbody tr");
  await expect(rows).toHaveCount(6);
  for (let i = 0; i < 6; i += 1) {
    await expect(rows.nth(i)).toContainText("%");
    await expect(rows.nth(i).locator("li")).toHaveCount(2);
  }
  await expect(page.locator("tr[data-risk=high]").first()).toBeVisible();
  await expect(page.locator("tr[data-risk=low]").first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/teacher-analytics.png`, fullPage: true });

  // The period narrows the charts; a week keeps the page alive.
  await page.getByLabel("Период").selectOption("7");
  await expect(page).toHaveURL(/days=7/);
  await expect(page.getByTestId("analytics-volume")).toBeVisible();

  // Dark theme keeps the charts readable (they follow the CSS variables).
  await page.getByRole("button", { name: /тему/i }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await expect(page.getByTestId("dynamics-score").locator("canvas")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/teacher-analytics-dark.png`, fullPage: true });
  await page.getByRole("button", { name: /тему/i }).click();

  await page.getByRole("link", { name: "Достоверность прогноза" }).click();
  await expect(page).toHaveURL(/readiness-model/);
  const metrics = page.getByTestId("model-metrics");
  await expect(metrics).toContainText("ROC AUC");
  await expect(metrics).toContainText("Brier");
  await expect(metrics).toContainText("300 виртуальных");
  await expect(page.getByTestId("calibration-chart").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("weights-chart").locator("canvas")).toBeVisible();
  await expect(page.getByText("Откуда данные")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/readiness-model.png`, fullPage: true });

  // Another teacher's group is an empty state, not an error.
  await page.goto("/teacher/analytics");
  await expect(page.getByTestId("analytics-volume")).toBeVisible();
  expect(problems).toEqual([]);
});

test("teacher: the session form offers adaptive selection", async ({ page }) => {
  await loginWithForm(page, "teacher1");
  await page.goto("/teacher/sessions/new");
  const box = page.getByLabel("Подбирать карточки под слабые места");
  await expect(box).not.toBeChecked();
  await box.check();
  await expect(box).toBeChecked();
});

test("trainee: my progress", async ({ page }) => {
  const problems = watchErrors(page);
  await loginWithForm(page, "student4");
  await page.getByRole("link", { name: "Прогресс" }).click();
  await expect(page).toHaveURL(/\/student\/progress/);
  await expect(page.getByTestId("progress-stats")).toContainText("Оценено");
  await expect(page.getByText("демонстрационные данные").first()).toBeVisible();
  // The tip about the weakest incident group comes first, then wave 9's advice.
  const tips = page.getByTestId("recommendations").locator("li");
  await expect(tips.first()).toContainText("Слабое место");
  await expect(tips).toHaveCount(4);
  await expect(page.getByTestId("ratings-chart").locator("canvas")).toBeVisible();
  await expect(page.getByRole("table", { name: "Занятия" }).locator("tbody tr").first()).toContainText("демонстрационные данные");
  await expect(page.getByTestId("progress-errors").locator("li").first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/student-progress.png`, fullPage: true });
  expect(problems).toEqual([]);
});

test("trainee without history sees the empty state", async ({ page }) => {
  await loginWithForm(page, "student7");
  await page.goto("/student/progress");
  await expect(page.getByText("Занятий с результатами пока нет.")).toBeVisible();
  await expect(page.getByTestId("ratings-chart")).toHaveCount(0);
});
