import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

// Wave 6 acceptance ("Проверка" of plan/wave-06.md) against the compose stand with the
// profiles ai and telephony. Chrome gets a fake microphone that plays an operator's question
// (e2e/fixtures/operator-question.wav), so the whole chain runs without a headset: softphone
// registers → scripts/issue_call.py issues a call-intake attempt → the call rings in the
// browser → answer → the caller's opening → the question is recognised → the caller answers.
// WebRTC statistics of the call are saved to docs/screenshots/wave-06/webrtc_stats.json.

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const SHOTS = resolve(ROOT, "docs/screenshots/wave-06");
const FIXTURE = resolve(HERE, "fixtures/operator-question.wav");
const EOL = String.fromCharCode(10);
const SIP_REGISTER_TIMEOUT = 20_000;
const CALL_TIMEOUT = 45_000;
const REPLY_TIMEOUT = 60_000;

test.use({
  launchOptions: {
    args: [
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
      `--use-file-for-fake-audio-capture=${FIXTURE}`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  },
  permissions: ["microphone"],
});

async function loginAsStudent(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "Войти как обучающийся" }).click();
  await expect(page.getByRole("heading", { name: "Мои задания" })).toBeVisible();
}

function issueCall(student = "student1") {
  execFileSync(
    "uv",
    ["run", "--project", "backend", "python", "scripts/issue_call.py", "--student", student, "--close-open"],
    { cwd: ROOT, stdio: "pipe", shell: process.platform === "win32" },
  );
}

test("софтфон регистрируется, принимает вызов и слышит заявителя", async ({ page }) => {
  test.setTimeout(240_000);
  mkdirSync(SHOTS, { recursive: true });
  const problems: string[] = [];
  const sipLog: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" || msg.type() === "warning") problems.push(`${msg.type()}: ${msg.text()}`);
    if (msg.text().includes("JsSIP")) sipLog.push(msg.text());
  });
  await page.addInitScript(() => localStorage.setItem("debug", "JsSIP:*"));
  page.on("pageerror", (err) => problems.push(`pageerror: ${err.message}`));
  page.on("websocket", (ws) => {
    if (ws.url().endsWith("/ws/sip")) ws.on("close", () => problems.push(`sip websocket closed at ${new Date().toISOString()}`));
  });
  await loginAsStudent(page);

  const badge = page.getByTestId("softphone-status");
  await expect(badge).toContainText("готов", { timeout: SIP_REGISTER_TIMEOUT });
  await expect(badge).toHaveAttribute("title", /Asterisk/);
  await page.screenshot({ path: resolve(SHOTS, "01-softphone-ready.png") });

  issueCall();
  const panel = page.getByTestId("call-panel");
  const visible = await panel.isVisible({ timeout: CALL_TIMEOUT }).catch(() => false);
  if (!visible) writeFileSync(resolve(SHOTS, "jssip-debug.log"), sipLog.join(EOL));
  await expect(panel, problems.join("; ")).toBeVisible({ timeout: CALL_TIMEOUT });
  await expect(panel).toContainText("входящий");
  await page.screenshot({ path: resolve(SHOTS, "02-incoming.png") });

  await page.getByTestId("call-answer").click();
  await expect(panel).toContainText("разговор", { timeout: CALL_TIMEOUT });
  // The caller speaks first: the opening shows up in the panel.
  await expect(panel).toContainText("Заявитель:", { timeout: REPLY_TIMEOUT });
  // The fake microphone asks «Что случилось? Диктуйте адрес.»: recognised text and a reply.
  await expect(panel).toContainText("Вы:", { timeout: REPLY_TIMEOUT });
  await page.screenshot({ path: resolve(SHOTS, "03-talking.png") });

  const stats = page.getByTestId("call-stats");
  await expect(stats).toContainText("RTT", { timeout: 10_000 });
  const statsText = (await stats.textContent()) ?? "";
  const rtt = /RTT (\d+) мс/.exec(statsText)?.[1];
  const jitter = /джиттер (\d+) мс/.exec(statsText)?.[1];
  const codec = statsText.split("·")[0].trim();
  writeFileSync(
    resolve(SHOTS, "webrtc_stats.json"),
    JSON.stringify({ at: new Date().toISOString(), browser: "chromium", codec, rtt_ms: Number(rtt), jitter_ms: Number(jitter), raw: statsText }, null, 2),
  );
  expect(Number(rtt)).toBeLessThan(150);

  await page.getByTestId("call-hangup").click();
  await expect(panel).toContainText("завершён", { timeout: 15_000 });
  await page.screenshot({ path: resolve(SHOTS, "04-ended.png") });
  expect(problems.filter((p) => p.startsWith("pageerror"))).toEqual([]);
});
