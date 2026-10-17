import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { vi } from "vitest";

import type { AttemptOut } from "@/api/training";
import type { CardCalls } from "@/emulator/use-card-calls";
import { VirtualPhone } from "@/emulator/virtual-phone";

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
function Phone({ overrides = {} }: { overrides?: Partial<CardCalls> }) {
  const [phoneOpen, setPhoneOpen] = useState(false);
  return <VirtualPhone attempt={attempt} calls={calls({ phoneOpen, setPhoneOpen, ...overrides })} />;
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
