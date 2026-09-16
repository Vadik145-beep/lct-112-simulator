import { screen, waitFor } from "@testing-library/react";
import { Route } from "react-router-dom";

import { AppShell } from "@/app/shell";
import { makeAuth, makeUser, renderAt } from "@/test/helpers";

function mockConfig(externalAi: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({ demo_mode: true, external_ai: externalAi, app_env: "test", dialog_mode: "select" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    ),
  );
}

function shell() {
  return (
    <Route element={<AppShell />}>
      <Route path="/student" element={<h1>Кабинет обучающегося</h1>} />
    </Route>
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("AppShell external AI banner", () => {
  it("warns in every cabinet when ALLOW_EXTERNAL_AI is on", async () => {
    mockConfig(true);
    renderAt(shell(), "/student", makeAuth({ status: "authenticated", user: makeUser() }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Внешняя модель: не для закрытого контура");
    expect(screen.getByRole("heading", { name: "Кабинет обучающегося" })).toBeInTheDocument();
  });

  it("shows nothing on the closed contour", async () => {
    mockConfig(false);
    renderAt(shell(), "/student", makeAuth({ status: "authenticated", user: makeUser() }));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
