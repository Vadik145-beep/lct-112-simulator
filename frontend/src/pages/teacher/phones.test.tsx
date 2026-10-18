import { render, screen, within } from "@testing-library/react";
import { vi } from "vitest";

import { TeacherPhonesPage } from "@/pages/teacher/phones";

const loaded = <T,>(data: T) => ({ isPending: false, isError: false, data });
const mutation = { mutate: vi.fn(), isPending: false, error: null };

vi.mock("@/api/phones", () => ({
  usePhoneDirectory: () =>
    loaded({
      entries: [
        { id: "1", service_code: "103", role: "head", role_title: "Руководитель организации", name: "А", number: "101" },
        { id: "2", service_code: "gkh", role: "head", role_title: "Руководитель организации", name: "Б", number: "3201" },
      ],
      roles: [{ code: "head", title: "Руководитель организации" }],
      max_per_service: 10,
    }),
  useAddPhone: () => mutation,
  useDeletePhone: () => mutation,
  useUpdatePhone: () => mutation,
}));
vi.mock("@/api/teacher", () => ({
  useServices: () =>
    loaded([
      { code: "101", short_title: "101", via_arm112: false },
      { code: "103", short_title: "103", via_arm112: false },
      { code: "fsb", short_title: "ФСБ", via_arm112: true },
      { code: "gkh", short_title: "Гор. хозяйство", via_arm112: true },
      { code: "territorial_oiv", short_title: "Управа", via_arm112: true },
    ]),
}));

describe("TeacherPhonesPage", () => {
  it("offers only the dispatch services a trainee works for", () => {
    render(<TeacherPhonesPage />);
    const select = screen.getByLabelText("Служба");
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Гор. хозяйство", "Управа"]);
  });
});
