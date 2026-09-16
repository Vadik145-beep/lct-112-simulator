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
import { TeacherGroupsPage } from "@/pages/teacher/groups";
import { SessionFormPage } from "@/pages/teacher/session-form";
import { SessionReportPage } from "@/pages/teacher/session-report";
import { TeacherSessionPage } from "@/pages/teacher/session";
import { TeacherSessionsPage } from "@/pages/teacher/sessions";
import { JournalPage } from "@/emulator/journal-page";
import { AttemptPage } from "@/intake/attempt-page";
import { CallsPage } from "@/intake/calls-page";
import { StudentFrame } from "@/softphone/student-frame";

export const router = createBrowserRouter([
  { path: "/", element: <HomePage /> },
  { path: "/login", element: <LoginPage /> },
  { path: "/change-password", element: <ChangePasswordPage /> },
  {
    element: <RequireRole roles={["student"]} />,
    children: [
      {
        // The softphone lives here so it stays registered across the cabinet and the АРМ.
        element: <StudentFrame />,
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
          { path: "/student/sessions/:sessionId/calls", element: <CallsPage /> },
          { path: "/student/attempts/:attemptId", element: <AttemptPage /> },
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
          { index: true, element: <TeacherSessionsPage /> },
          { path: "sessions/new", element: <SessionFormPage /> },
          { path: "sessions/:sessionId", element: <TeacherSessionPage /> },
          { path: "sessions/:sessionId/edit", element: <SessionFormPage /> },
          { path: "sessions/:sessionId/report", element: <SessionReportPage /> },
          { path: "attempts/:attemptId/review", element: <AttemptReviewPage /> },
          { path: "groups", element: <TeacherGroupsPage /> },
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
