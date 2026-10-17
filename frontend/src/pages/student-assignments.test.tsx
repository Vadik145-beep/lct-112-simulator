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

function open(entry: boolean) {
  const user = makeUser({ role: "student" });
  render(
    <AuthContext.Provider value={makeAuth({ status: "authenticated", user })}>
      <MemoryRouter initialEntries={[{ pathname: "/student", state: entry ? { entry: true } : null }]}>
        <Routes>
          <Route path="/student" element={<StudentAssignmentsPage />} />
          <Route path="/student/sessions/:sessionId/journal" element={<p>Журнал АРМ</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("Where the trainee lands after the login", () => {
  it("goes straight to the journal of the one running lesson", () => {
    lessons.current = [lesson("a"), lesson("b", "finished")];
    open(true);
    expect(screen.getByText("Журнал АРМ")).toBeInTheDocument();
  });

  it("stays on «Мои задания» when several lessons run", () => {
    lessons.current = [lesson("a"), lesson("b")];
    open(true);
    expect(screen.getByRole("heading", { name: "Мои задания" })).toBeInTheDocument();
  });

  it("stays on «Мои задания» when none runs", () => {
    lessons.current = [lesson("a", "finished")];
    open(true);
    expect(screen.getByRole("heading", { name: "Мои задания" })).toBeInTheDocument();
  });

  it("does not send back to the journal a trainee who came from it to the cabinet", () => {
    lessons.current = [lesson("a")];
    open(false);
    expect(screen.getByRole("heading", { name: "Мои задания" })).toBeInTheDocument();
  });
});
