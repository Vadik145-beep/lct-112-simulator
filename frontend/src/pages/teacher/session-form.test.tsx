import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AuthContext } from "@/app/auth-context";
import { SessionFormPage } from "@/pages/teacher/session-form";
import { makeAuth, makeUser } from "@/test/helpers";

const existing = { current: null as Record<string, unknown> | null };

const loaded = (data: unknown) => ({ isPending: false, isError: false, data });
const mutation = { isPending: false, isError: false, mutate: vi.fn() };

vi.mock("@/api/teacher", () => ({
  useGroups: () => loaded([{ id: "g1", title: "Группа 1", members: [] }]),
  useClassifierTree: () => loaded({ groups: [] }),
  useServices: () => loaded([]),
  useModels: () =>
    loaded({ dialog: true, tts: true, stt: true, cloud: false, phone: false }),
  useQueuePreview: () => loaded({ total: 3, harder_only: false }),
  useCreateSession: () => mutation,
  useUpdateSession: () => mutation,
  useTeacherSession: () => loaded(existing.current),
}));

function draft(dialogMode: string) {
  return {
    id: "s1",
    title: "Старое занятие",
    status: "draft",
    mode: "call_intake",
    group_id: "g1",
    card_source: "scenarios",
    scenario_ids: [],
    incident_groups: [],
    difficulty: 1,
    service_profile: [],
    norm_seconds: 60,
    pass_threshold: 70,
    hints_enabled: true,
    cards_per_student: 0,
    unfinished_seconds: 172800,
    weights: {},
    voice_enabled: true,
    dialog_mode: dialogMode,
    adaptive: false,
    phone_calls: false,
  };
}

function renderForm(path: string, callIntakeAccess = true) {
  const user = makeUser({ role: "teacher", call_intake_access: callIntakeAccess });
  return render(
    <AuthContext.Provider value={makeAuth({ status: "authenticated", user })}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/teacher/sessions/new" element={<SessionFormPage />} />
          <Route
            path="/teacher/sessions/:sessionId/edit"
            element={<SessionFormPage />}
          />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

function modeOptions() {
  const select = document.getElementById("dialog-mode") as HTMLSelectElement;
  return within(select)
    .getAllByRole("option")
    .map((o) => (o as HTMLOptionElement).value);
}

describe("Dialog mode of a lesson", () => {
  it("does not offer «Кнопки тем» for a new lesson, in either mode", () => {
    renderForm("/teacher/sessions/new");
    expect(modeOptions()).toEqual(["select", "generate"]);
    fireEvent.click(screen.getByLabelText(/Реагирование на карточку/));
    expect(modeOptions()).toEqual(["select", "generate"]);
  });

  it("keeps «Кнопки тем» shown for a lesson that already runs in it", () => {
    existing.current = draft("buttons");
    renderForm("/teacher/sessions/s1/edit");
    const select = document.getElementById("dialog-mode") as HTMLSelectElement;
    expect(select.value).toBe("buttons");
    expect(modeOptions()).toContain("buttons");
  });

  it("does not offer «Готовые + новые» for a new lesson but keeps it for a lesson that has it", () => {
    existing.current = draft("hybrid");
    renderForm("/teacher/sessions/s1/edit");
    const select = document.getElementById("dialog-mode") as HTMLSelectElement;
    expect(select.value).toBe("hybrid");
    expect(modeOptions()).toEqual(["select", "hybrid", "generate"]);
  });

  it("does not add it to a lesson in another mode", () => {
    existing.current = draft("select");
    renderForm("/teacher/sessions/s1/edit");
    expect(modeOptions()).toEqual(["select", "generate"]);
  });
});

describe("Lesson mode and access to the 112 operator mode", () => {
  it("offers both modes to a teacher the administrator opened 112 for", () => {
    existing.current = null;
    renderForm("/teacher/sessions/new");
    expect(screen.getByLabelText(/Приём вызова/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Реагирование на карточку/)).toBeInTheDocument();
  });

  it("hides the mode choice from a teacher without access", () => {
    existing.current = null;
    renderForm("/teacher/sessions/new", false);
    expect(screen.queryByText("Режим")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Приём вызова/)).not.toBeInTheDocument();
    expect(screen.getByText("Как отвечают служба и бригада")).toBeInTheDocument();
  });
});
