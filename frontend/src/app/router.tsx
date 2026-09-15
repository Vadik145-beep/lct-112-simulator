import { createBrowserRouter } from "react-router-dom";

import { RequireRole } from "@/app/require-role";
import { AppShell } from "@/app/shell";
import { CabinetPage } from "@/pages/cabinet";
import { ChangePasswordPage } from "@/pages/change-password";
import { HomePage } from "@/pages/home";
import { LoginPage } from "@/pages/login";
import { NotFoundPage } from "@/pages/not-found";
import { PlaceholderPage } from "@/pages/placeholder";

export const router = createBrowserRouter([
  { path: "/", element: <HomePage /> },
  { path: "/login", element: <LoginPage /> },
  { path: "/change-password", element: <ChangePasswordPage /> },
  {
    element: <RequireRole roles={["student"]} />,
    children: [
      {
        path: "/student",
        element: <AppShell />,
        children: [
          { index: true, element: <CabinetPage role="student" /> },
          { path: "progress", element: <PlaceholderPage title="Прогресс" /> },
        ],
      },
    ],
  },
  {
    element: <RequireRole roles={["teacher"]} />,
    children: [
      {
        path: "/teacher",
        element: <AppShell />,
        children: [
          { index: true, element: <CabinetPage role="teacher" /> },
          { path: "groups", element: <PlaceholderPage title="Группы" /> },
          { path: "scenarios", element: <PlaceholderPage title="Сценарии" /> },
        ],
      },
    ],
  },
  {
    element: <RequireRole roles={["admin"]} />,
    children: [
      {
        path: "/admin",
        element: <AppShell />,
        children: [
          { index: true, element: <CabinetPage role="admin" /> },
          { path: "health", element: <PlaceholderPage title="Состояние сервисов" /> },
          { path: "audit", element: <PlaceholderPage title="Журнал аудита" /> },
        ],
      },
    ],
  },
  { path: "*", element: <NotFoundPage /> },
]);
