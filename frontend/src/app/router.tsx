import { createBrowserRouter } from "react-router-dom";

import { RequireRole } from "@/app/require-role";
import { AppShell } from "@/app/shell";
import { CabinetPage } from "@/pages/cabinet";
import { ChangePasswordPage } from "@/pages/change-password";
import { HomePage } from "@/pages/home";
import { LoginPage } from "@/pages/login";
import { NotFoundPage } from "@/pages/not-found";
import { PlaceholderPage } from "@/pages/placeholder";
import { AttemptReviewPage } from "@/pages/attempt-review";
import { StudentAssignmentsPage } from "@/pages/student-assignments";
import { StudentReferencePage } from "@/pages/student-reference";
import { CardPage } from "@/emulator/card-page";
import { JournalPage } from "@/emulator/journal-page";

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
          { index: true, element: <StudentAssignmentsPage /> },
          { path: "progress", element: <PlaceholderPage title="Прогресс" /> },
          { path: "reference", element: <StudentReferencePage /> },
          { path: "attempts/:attemptId/review", element: <AttemptReviewPage /> },
        ],
      },
      // The АРМ-112 emulator has its own chrome: no cabinet shell around it.
      { path: "/student/sessions/:sessionId/journal", element: <JournalPage /> },
      { path: "/student/attempts/:attemptId", element: <CardPage /> },
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
