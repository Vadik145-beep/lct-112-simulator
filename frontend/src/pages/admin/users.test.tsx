import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AdminUsersPage } from "@/pages/admin/users";

const loaded = (data: unknown) => ({ isPending: false, isError: false, data });
const mutation = { isPending: false, mutate: vi.fn() };

vi.mock("@/api/admin", () => ({
  useAdminUsers: () =>
    loaded([
      {
        id: "u1",
        login: "student",
        display_name: "З. Ф. В.",
        role: "student",
        service_code: "gkh",
        is_blocked: false,
        must_change_password: false,
        has_sip_account: false,
        call_intake_access: false,
        created_at: "2026-10-01T00:00:00Z",
        last_login_at: null,
      },
    ]),
  useAdminServices: () =>
    loaded([{ code: "gkh", short_title: "Гор. хозяйство", title: "Городское хозяйство (ЕДЦ ЖКХ)" }]),
  useCreateUser: () => mutation,
  useResetPassword: () => mutation,
  useRevealUser: () => mutation,
  useSipAccount: () => mutation,
  useUpdateUser: () => mutation,
}));

describe("Users of the administrator", () => {
  it("names the trainee's service instead of its code", () => {
    render(
      <MemoryRouter>
        <AdminUsersPage />
      </MemoryRouter>,
    );
    const row = screen.getByRole("row", { name: /student/ });
    expect(within(row).getByText("Гор. хозяйство")).toHaveAttribute("title", "Городское хозяйство (ЕДЦ ЖКХ)");
    expect(within(row).queryByText("gkh")).not.toBeInTheDocument();
  });
});
