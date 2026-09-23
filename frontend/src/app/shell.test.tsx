import { screen } from "@testing-library/react";
import { Route } from "react-router-dom";

import { AppShell } from "@/app/shell";
import { makeAuth, makeUser, renderAt } from "@/test/helpers";

function shell() {
  return (
    <Route element={<AppShell />}>
      <Route path="/student" element={<h1>Кабинет обучающегося</h1>} />
    </Route>
  );
}

describe("AppShell", () => {
  it("shows the cabinet under the common header", () => {
    renderAt(
      shell(),
      "/student",
      makeAuth({ status: "authenticated", user: makeUser() }),
    );
    expect(
      screen.getByRole("heading", { name: "Кабинет обучающегося" }),
    ).toBeInTheDocument();
    // The external-model banner was removed (22.09.2026): the режимы с выходом в интернет
    // are explained to the experts in person, see PRD section 2.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
