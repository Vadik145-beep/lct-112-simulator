import { screen } from "@testing-library/react";
import { Route } from "react-router-dom";

import { RequireRole } from "@/app/require-role";
import { makeAuth, makeUser, renderAt } from "@/test/helpers";

function teacherArea() {
  return (
    <Route element={<RequireRole roles={["teacher"]} />}>
      <Route path="/teacher" element={<h1>Кабинет преподавателя</h1>} />
    </Route>
  );
}

const loginRoute = <Route path="/login" element={<h1>Страница входа</h1>} />;
const changeRoute = <Route path="/change-password" element={<h1>Смена пароля</h1>} />;

describe("RequireRole", () => {
  it("shows the page to the right role", () => {
    renderAt(teacherArea(), "/teacher", makeAuth({ status: "authenticated", user: makeUser({ role: "teacher" }) }));
    expect(screen.getByRole("heading", { name: "Кабинет преподавателя" })).toBeInTheDocument();
  });

  it("shows «Нет доступа» to a student", () => {
    renderAt(teacherArea(), "/teacher", makeAuth({ status: "authenticated", user: makeUser({ role: "student" }) }));
    expect(screen.getByRole("heading", { name: "Нет доступа" })).toBeInTheDocument();
    expect(screen.queryByText("Кабинет преподавателя")).not.toBeInTheDocument();
  });

  it("sends anonymous users to the login page", () => {
    renderAt(teacherArea(), "/teacher", makeAuth({ status: "anonymous" }), loginRoute);
    expect(screen.getByRole("heading", { name: "Страница входа" })).toBeInTheDocument();
  });

  it("forces a password change first when the flag is set", () => {
    const user = makeUser({ role: "teacher", must_change_password: true });
    renderAt(teacherArea(), "/teacher", makeAuth({ status: "authenticated", user }), changeRoute);
    expect(screen.getByRole("heading", { name: "Смена пароля" })).toBeInTheDocument();
  });

  it("shows a loading state while the session is being restored", () => {
    renderAt(teacherArea(), "/teacher", makeAuth({ status: "loading" }));
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
