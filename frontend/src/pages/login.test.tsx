import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";

import { LoginPage } from "@/pages/login";
import { makeUser } from "@/test/helpers";
import { makeAuth, renderAt } from "@/test/helpers";

function mockConfig(demoMode: boolean, quickLoginRoles: string[] = demoMode ? ["student", "teacher", "admin"] : []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify({ demo_mode: demoMode, quick_login_roles: quickLoginRoles, external_ai: false, app_env: "test" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
}

const loginRoute = <Route path="/login" element={<LoginPage />} />;

afterEach(() => vi.unstubAllGlobals());

describe("LoginPage", () => {
  it("sends a logged-in teacher to the teacher cabinet even if they came from /student", () => {
    mockConfig(true);
    const auth = makeAuth({ status: "authenticated", user: makeUser({ role: "teacher" }) });
    renderAt(
      <>
        {loginRoute}
        <Route path="/teacher" element={<h1>Кабинет преподавателя</h1>} />
        <Route path="/student" element={<h1>Кабинет обучающегося</h1>} />
      </>,
      { pathname: "/login", state: { from: "/student" } },
      auth,
    );
    expect(screen.getByRole("heading", { name: "Кабинет преподавателя" })).toBeInTheDocument();
  });

  it("does not offer the form while a previous session is being restored", () => {
    mockConfig(true);
    renderAt(loginRoute, "/login", makeAuth({ status: "loading" }));
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Войти/ })).not.toBeInTheDocument();
  });

  it("shows demo buttons when DEMO_MODE is on and calls demoLogin", async () => {
    mockConfig(true);
    const auth = makeAuth();
    renderAt(loginRoute, "/login", auth);
    const button = await screen.findByRole("button", { name: /Войти как преподаватель/ });
    await userEvent.click(button);
    expect(auth.demoLogin).toHaveBeenCalledWith("teacher");
  });

  it("hides demo buttons when DEMO_MODE is off", async () => {
    mockConfig(false);
    renderAt(loginRoute, "/login", makeAuth());
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /Войти как/ })).not.toBeInTheDocument();
  });

  it("offers one-click entry as teacher and student, not admin, when QUICK_LOGIN is on", async () => {
    mockConfig(false, ["teacher", "student"]);
    const auth = makeAuth();
    renderAt(loginRoute, "/login", auth);
    const teacher = await screen.findByRole("button", { name: /Войти как преподаватель/ });
    expect(screen.getByRole("button", { name: /Войти как обучающийся/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Войти как администратор/ })).not.toBeInTheDocument();
    expect(screen.getByText("Вход в демонстрационный стенд без пароля")).toBeInTheDocument();
    await userEvent.click(teacher);
    expect(auth.demoLogin).toHaveBeenCalledWith("teacher");
  });

  it("lists logins and passwords by role with a one-click entry for each", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            demo_mode: false,
            quick_login_roles: ["teacher", "student", "admin"],
            demo_credentials: [
              { role: "admin", login: "admin", password: "112112112" },
              { role: "teacher", login: "teacher", password: "112112112" },
              { role: "student", login: "student", password: "112112112" },
            ],
            external_ai: false,
            app_env: "test",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const auth = makeAuth();
    renderAt(loginRoute, "/login", auth);
    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(rows[0]).toContain("Преподаватель");
    expect(rows[0]).toContain("teacher");
    expect(rows[2]).toContain("Администратор");
    expect(screen.getAllByText("112112112")).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: "Войти как администратор" }));
    expect(auth.demoLogin).toHaveBeenCalledWith("admin");
  });

  it("submits login and password and shows the server message on failure", async () => {
    mockConfig(false);
    const auth = makeAuth({ login: vi.fn(async () => { throw new Error("Неверный логин или пароль."); }) });
    renderAt(loginRoute, "/login", auth);
    await userEvent.type(screen.getByLabelText("Логин"), "student1");
    await userEvent.type(screen.getByLabelText("Пароль"), "secret");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(auth.login).toHaveBeenCalledWith("student1", "secret");
    expect(await screen.findByRole("alert")).toHaveTextContent("Неверный логин или пароль.");
  });
});
