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
  useClassifierTree: () =>
    loaded({
      groups: [
        { code: "1", title: "Пожары и задымления" },
        { code: "13", title: "Запах газа" },
      ],
    }),
  useServices: () =>
    loaded([
      { code: "gkh", title: "ЕДЦ ЖКХ", short_title: "ЕДЦ ЖКХ" },
      { code: "dgp", title: "ДГП (АРМ-112)", short_title: "ДГП" },
    ]),
  useAiCardGroups: () => loaded({ by_service: { gkh: { "1": 127 }, dgp: { "1": 43 } } }),
  useModels: () =>
    loaded({ dialog: true, tts: true, stt: true, cloud: false, phone: false }),
  useQueuePreview: () =>
    loaded({ total: 3, harder_only: false, queue: [{ service_code: "gkh", incident_type_code: "14.1" }] }),
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

describe("Card stream at difficulty 3", () => {
  it("says the cards come as a stream, with no fields of its own", () => {
    existing.current = null;
    renderForm("/teacher/sessions/new", false);
    fireEvent.change(screen.getByLabelText("Сложность"), { target: { value: "3" } });
    expect(screen.getByText("Вал: 5 карточек: первая сразу, дальше по одной каждые 20–25 с.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Сложность"), { target: { value: "1" } });
    expect(screen.getByText("Каждый обучающийся получит одну карточку.")).toBeInTheDocument();
  });
});

describe("«Задания формирует ИИ»", () => {
  it("sends the lesson as written for each trainee, five cards; at difficulty 3 as the same stream", () => {
    existing.current = null;
    mutation.mutate.mockClear();
    renderForm("/teacher/sessions/new", false);
    fireEvent.change(screen.getByLabelText("Название"), { target: { value: "ИИ" } });
    fireEvent.click(screen.getByLabelText("Задания формирует ИИ"));
    fireEvent.change(screen.getByLabelText("Сложность"), { target: { value: "3" } });
    expect(
      screen.getByText(
        "Вал своих карточек каждому — 5 карточек: первая сразу, дальше по одной каждые 20–25 с; ЧС, по две ошибки оператора 112 в карточке.",
      ),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Сложность"), { target: { value: "2" } });
    expect(screen.getByText("Каждый обучающийся получит 5 своих карточек по одной.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Сложность"), { target: { value: "3" } });
    expect(screen.getByText(/готовые сценарии не нужны/)).toBeInTheDocument();
    // Whose ДДС the trainees are is required (decision of 17.10.2026).
    expect(screen.getByRole("button", { name: "Создать занятие" })).toBeDisabled();
    fireEvent.click(screen.getByLabelText("ЕДЦ ЖКХ"));
    fireEvent.click(screen.getByRole("button", { name: "Создать занятие" }));
    expect(mutation.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ card_source: "ai", cards_per_student: 5, difficulty: 3, service_profile: ["gkh"] }),
      expect.anything(),
    );
  });

  it("offers only the groups the chosen service takes part in, with the number of types", () => {
    existing.current = null;
    mutation.mutate.mockClear();
    renderForm("/teacher/sessions/new", false);
    fireEvent.change(screen.getByLabelText("Название"), { target: { value: "ИИ" } });
    fireEvent.click(screen.getByLabelText("Задания формирует ИИ"));
    expect(screen.getByText("ДДС какой службы")).toBeInTheDocument();
    // Only the services the trainer is prepared for, not every organization of the classifier.
    expect(screen.queryByLabelText("ДГП (АРМ-112)")).not.toBeInTheDocument();
    expect(screen.getByText("Сначала выберите службу ДДС.")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Пожары и задымления/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("ЕДЦ ЖКХ"));
    expect(screen.getByLabelText(/Пожары и задымления/)).toBeInTheDocument();
    expect(screen.getByText(/видов: 127/)).toBeInTheDocument();
    // ЖКХ never gets «Запах газа»: it cannot be checked, so it is never ignored silently.
    expect(screen.queryByLabelText(/Запах газа/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/Пожары и задымления/));
    fireEvent.click(screen.getByRole("button", { name: "Создать занятие" }));
    expect(mutation.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ service_profile: ["gkh"], incident_groups: ["1"] }),
      expect.anything(),
    );
  });

  it("keeps the choice when a draft is edited", () => {
    existing.current = { ...draft("select"), mode: "card_response", card_source: "ai" };
    renderForm("/teacher/sessions/s1/edit");
    expect(screen.getByLabelText("Задания формирует ИИ")).toBeChecked();
  });

  it("is not offered for the 112 operator mode", () => {
    existing.current = null;
    renderForm("/teacher/sessions/new");
    expect(screen.getByLabelText("Задания формирует ИИ")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/Приём вызова/));
    expect(screen.queryByLabelText("Задания формирует ИИ")).not.toBeInTheDocument();
  });
});
