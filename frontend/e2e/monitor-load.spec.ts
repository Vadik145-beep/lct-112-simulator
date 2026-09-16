import { expect, test } from "@playwright/test";

// Load rehearsal of the live monitoring (plan wave 4, «Проверка»): while
// `python -m app.load_monitor --students 20 --seconds 90 --keep` runs inside the backend
// container, the teacher's page must keep up with ~20 events per second and, after a
// 10-second outage, catch up with the server's snapshot.
//
// Run by hand:  E2E_BASE_URL=https://localhost:8443 npx playwright test e2e/monitor-load.spec.ts
// It is skipped unless MONITOR_LOAD=1, so scripts/check.sh does not depend on the load script.

const PASSWORD = process.env.SEED_PASSWORD ?? "Demo12345";

test.skip(!process.env.MONITOR_LOAD, "нужен запущенный app.load_monitor; включается MONITOR_LOAD=1");

test("мониторинг 20 обучающихся не тормозит и догоняет после обрыва", async ({ page, request, context }) => {
  test.setTimeout(180_000);
  const res = await request.post("/api/auth/login", { data: { login: "teacher1", password: PASSWORD } });
  const token = ((await res.json()) as { access_token: string }).access_token;
  const headers = { Authorization: `Bearer ${token}` };
  const sessions = (await (await request.get("/api/sessions", { headers })).json()) as { id: string; title: string; status: string }[];
  const load = sessions.find((s) => s.status === "running" && s.title.startsWith("Нагрузка мониторинга"));
  expect(load, "запустите app.load_monitor --keep").toBeTruthy();

  // Chromium's offline mode does not drop an open WebSocket, so the sockets are kept in
  // the page and the last one is broken by hand (the server closes on a malformed frame).
  await page.addInitScript(() => {
    type Sock = { send(data: string): void; readyState: number };
    const g = globalThis as unknown as { __sockets: Sock[]; WebSocket: new (...args: unknown[]) => Sock };
    const sockets: Sock[] = [];
    g.__sockets = sockets;
    g.WebSocket = new Proxy(g.WebSocket, {
      construct(target, args) {
        const socket = Reflect.construct(target, args) as Sock;
        sockets.push(socket);
        return socket;
      },
    });
  });
  await page.goto("/login");
  await page.getByRole("button", { name: "Войти как преподаватель" }).click();
  await expect(page.getByRole("heading", { name: "Занятия" })).toBeVisible();
  await page.goto(`/teacher/sessions/${load!.id}`);
  const tiles = page.getByRole("list", { name: "Обучающиеся" }).getByRole("listitem");
  await expect(tiles).toHaveCount(20);

  // Responsiveness: the page keeps answering (a click on the header) while events stream.
  const samples: number[] = [];
  for (let i = 0; i < 10; i++) {
    const started = Date.now();
    await page.getByRole("heading", { level: 1 }).click();
    await page.evaluate(() => new Promise((r) => setTimeout(r, 0)));
    samples.push(Date.now() - started);
    await page.waitForTimeout(1000);
  }
  const worst = Math.max(...samples);
  console.log("время отклика страницы, мс:", samples.join(", "));
  expect(worst).toBeLessThan(500);

  // Outage: 10 s offline, the banner shows, then the tiles converge with the server.
  const sockets = await page.evaluate(() => {
    const list = (globalThis as unknown as { __sockets: { send(d: string): void; readyState: number }[] }).__sockets;
    list.at(-1)?.send("{");
    return list.map((s) => s.readyState);
  });
  console.log("сокеты страницы:", sockets.join(", "));
  await context.setOffline(true);
  await expect(page.getByRole("status").filter({ hasText: "Связь потеряна" })).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(10_000);
  await context.setOffline(false);
  await expect(page.getByRole("status").filter({ hasText: "Связь потеряна" })).toBeHidden({ timeout: 20_000 });
  await page.waitForTimeout(3000);

  const snapshot = (await (await request.get(`/api/sessions/${load!.id}/monitor`, { headers })).json()) as {
    students: { login: string; finished: number }[];
  };
  const shown = await tiles.evaluateAll((items) =>
    items.map((el) => {
      const button = el.querySelector("button");
      const m = /закрыто (\d+)/.exec(el.textContent ?? "");
      return { login: button?.getAttribute("data-student") ?? "", finished: m ? Number(m[1]) : -1 };
    }),
  );
  const server = new Map(snapshot.students.map((s) => [s.login, s.finished]));
  let behind = 0;
  for (const tile of shown) {
    const expected = server.get(tile.login) ?? -2;
    // The stream keeps running: the page may be at most one card behind the snapshot.
    if (tile.finished < expected - 1) behind++;
  }
  console.log("плиток отстаёт от сервера:", behind, "из", shown.length);
  expect(behind).toBe(0);
});
