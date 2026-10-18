import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AuthContext } from "@/app/auth-context";
import { StudentAssignmentsPage } from "@/pages/student-assignments";
import { makeAuth, makeUser } from "@/test/helpers";

const lessons = { current: [] as Record<string, unknown>[] };

vi.mock("@/api/training", () => ({
  useAssignments: () => ({ isPending: false, isError: false, data: lessons.current }),
}));
vi.mock("@/api/telephony", () => ({
  useMyProfile: () => ({ isPending: false, isError: false, data: { phone: null } }),
  useSetMyPhone: () => ({ isPending: false, mutate: vi.fn() }),
}));

function lesson(id: string, status = "running") {
  return {
    id,
    title: `Занятие ${id}`,
    mode: "card_response",
    status,
    service: { title: "ДДС управы" },
    norm_seconds: 30,
    active_cards: 0,
    finished_cards: 0,
    started_at: null,
    phone_calls: false,
    lesson_phone: null,
  };
}

function open() {
  const user = makeUser({ role: "student" });
  render(
    <AuthContext.Provider value={makeAuth({ status: "authenticated", user })}>
      <MemoryRouter initialEntries={["/student"]}>
        <Routes>
          <Route path="/student" element={<StudentAssignmentsPage />} />
          <Route path="/student/sessions/:sessionId/journal" element={<p>Журнал АРМ</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("Where the trainee lands after the login", () => {
  it("stays on «Мои задания» even with one running lesson, the journal is one click away", () => {
    lessons.current = [lesson("a"), lesson("b", "finished")];
    open();
    expect(screen.getByRole("heading", { name: "Мои задания" })).toBeInTheDocument();
    expect(screen.queryByText("Журнал АРМ")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Открыть журнал АРМ-112/ })).toHaveAttribute(
      "href",
      "/student/sessions/a/journal",
    );
  });
});
