import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { vi } from "vitest";

import type { AttemptOut } from "@/api/training";
import type { CardCalls } from "@/emulator/use-card-calls";
import { VirtualPhone } from "@/emulator/virtual-phone";

const ringing = { current: [] as unknown[] };
const navigate = vi.fn();
const location = { current: { pathname: "/student/attempts/a1", state: null as unknown } };
vi.mock("@/api/training", () => ({ useRinging: () => ({ data: ringing.current }) }));
vi.mock("react-router-dom", () => ({
  useNavigate: () => navigate,
  useLocation: () => location.current,
}));

vi.mock("@/api/phones", () => ({
  usePhoneBook: () => ({
    isSuccess: true,
    isError: false,
    data: {
      service: "moek",
      entries: [
        { role: "head", role_title: "Руководитель организации", name: "Соколов А. П.", number: "2101" },
        { role: "squad_chief", role_title: "Начальник аварийной бригады", name: "Кузнецов Н. И.", number: "2103" },
        { role: "caller", role_title: "Заявитель", name: "номер из карточки", number: null },
      ],
    },
  }),
}));

const attempt = {
  id: "a1",
  session: { id: "s1" },
  service_calls: [],
  card: { services: [{ code: "moek", is_own: true }] },
  reports_expected: false,
  response_status: "received",
} as unknown as AttemptOut;

function calls(overrides: Partial<CardCalls> = {}): CardCalls {
  return {
    softphone: null,
    finished: false,
    cloudLesson: false,
    cloudState: "off",
    cloudMuted: false,
    toggleCloudMute: () => {},
    sttAvailable: false,
    openCall: null,
    shownCall: null,
    showCall: () => {},
    busy: false,
    replying: false,
    error: null,
    dialError: null,
    resetDialError: () => {},
    canCall: true,
    phoneOpen: false,
    setPhoneOpen: () => {},
    call: () => {},
    dial: () => {},
    answer: () => {},
    hangUp: () => {},
    say: () => {},
    speak: () => {},
    ...overrides,
  } as CardCalls;
}

/** The phone with the open state of the shared hook kept in a real component. */
function Phone({
  overrides = {},
  card = attempt,
}: {
  overrides?: Partial<CardCalls>;
  card?: AttemptOut;
}) {
  const [phoneOpen, setPhoneOpen] = useState(false);
  return <VirtualPhone attempt={card} calls={calls({ phoneOpen, setPhoneOpen, ...overrides })} />;
}

describe("VirtualPhone", () => {
  it("opens from the green circle and dials the number typed on the keypad", () => {
    const dial = vi.fn();
    render(<Phone overrides={{ dial }} />);
    expect(screen.queryByTestId("virtual-phone")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Телефон" }));
    for (const key of ["2", "1", "0", "3"]) fireEvent.click(screen.getByRole("button", { name: key }));
    expect(screen.getByLabelText("Набираемый номер")).toHaveValue("2103");
    fireEvent.click(screen.getByRole("button", { name: /Вызов/ }));
    expect(dial).toHaveBeenCalledWith("2103");
  });

  it("clears the number once the call goes through", () => {
    const { rerender } = render(<Phone />);
    fireEvent.click(screen.getByRole("button", { name: "Телефон" }));
    for (const key of ["3", "8", "0", "3"]) fireEvent.click(screen.getByRole("button", { name: key }));
    const call = { id: "c1", kind: "outgoing", answered: true, ended_at: null } as CardCalls["openCall"];
    rerender(<Phone overrides={{ openCall: call }} />);
    rerender(<Phone overrides={{ openCall: null }} />);
    expect(screen.getByLabelText("Набираемый номер")).toHaveValue("");
  });

  it("fills the number from the book and keeps the caller's line as a hint", () => {
    render(<Phone />);
    fireEvent.click(screen.getByRole("button", { name: "Телефон" }));
    fireEvent.click(screen.getByRole("button", { name: /Руководитель организации/ }));
    expect(screen.getByLabelText("Набираемый номер")).toHaveValue("2101");
    // The caller's number is not in the book: it is dialled from the card.
    expect(screen.getByText("номер из карточки")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Заявитель/ })).toBeNull();
  });

  it("shows why a number did not go through", () => {
    const dialError = new Error("Набранный номер не существует.");
    render(<Phone overrides={{ dialError } as Partial<CardCalls>} />);
    fireEvent.click(screen.getByRole("button", { name: "Телефон" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Набранный номер не существует.");
  });

  it("searches the book by role, name or number", () => {
    render(<Phone />);
    fireEvent.click(screen.getByRole("button", { name: "Телефон" }));
    fireEvent.change(screen.getByLabelText("Поиск по справочнику"), { target: { value: "бригад" } });
    expect(screen.getByRole("button", { name: /Начальник аварийной бригады/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Руководитель организации/ })).toBeNull();
  });
});


describe("VirtualPhone and the other cards", () => {
  it("rings in orange about another card and leads to it", () => {
    ringing.current = [
      {
        attempt_id: "a2",
        call_id: "c2",
        card_number: "34451676",
        incident_title: "Взрывы",
        address: "3-я Сосновая улица, 57",
      },
    ];
    render(<Phone />);
    const circle = screen.getByTestId("virtual-phone-button");
    expect(circle).toHaveAccessibleName("Телефон: звонок по другой карточке");
    fireEvent.click(circle);
    expect(screen.getByTestId("other-card-calls")).toHaveTextContent(
      "Звонок по другой карточке № 34451676",
    );
    fireEvent.click(screen.getByRole("button", { name: "Перейти в карточку и ответить" }));
    expect(navigate).toHaveBeenCalledWith("/student/attempts/a2", {
      state: { answerCallId: "c2" },
    });
    ringing.current = [];
  });
});

describe("VirtualPhone after «Перейти в карточку и ответить»", () => {
  const report = {
    id: "c2",
    kind: "report",
    answered: false,
    ended_at: null,
  } as CardCalls["openCall"];

  afterEach(() => {
    location.current = { pathname: "/student/attempts/a1", state: null };
    navigate.mockClear();
  });

  it("picks up the ringing call at once, one time only (стенд 18.10.2026, Б36)", () => {
    location.current = { pathname: "/student/attempts/a1", state: { answerCallId: "c2" } };
    const answer = vi.fn();
    const { rerender } = render(<Phone overrides={{ openCall: report, answer }} />);
    expect(answer).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith("/student/attempts/a1", { replace: true, state: null });
    // A new render before the state is cleared does not pick up twice.
    rerender(<Phone overrides={{ openCall: report, answer: () => answer() }} />);
    expect(answer).toHaveBeenCalledTimes(1);
  });

  it("waits while the card does not show the call yet", () => {
    location.current = { pathname: "/student/attempts/a1", state: { answerCallId: "c2" } };
    const answer = vi.fn();
    const { rerender } = render(<Phone overrides={{ answer }} />);
    expect(answer).not.toHaveBeenCalled();
    rerender(<Phone overrides={{ openCall: report, answer }} />);
    expect(answer).toHaveBeenCalledTimes(1);
  });

  it("leaves a call that stopped ringing meanwhile", () => {
    location.current = { pathname: "/student/attempts/a1", state: { answerCallId: "c2" } };
    const answer = vi.fn();
    const ended = { ...report, ended_at: "2026-10-18T10:41:30Z" };
    const card = { ...attempt, service_calls: [ended] } as unknown as AttemptOut;
    render(<Phone card={card} overrides={{ answer }} />);
    expect(answer).not.toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith("/student/attempts/a1", { replace: true, state: null });
  });
});
