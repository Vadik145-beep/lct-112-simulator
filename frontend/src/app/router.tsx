import { createBrowserRouter } from "react-router-dom";

import { RequireRole } from "@/app/require-role";
import { AppShell } from "@/app/shell";
import { AdminAuditPage } from "@/pages/admin/audit";
import { AdminBackupsPage } from "@/pages/admin/backups";
import { AdminHealthPage } from "@/pages/admin/health";
import { AdminServicesPage } from "@/pages/admin/services";
import { AdminSettingsPage } from "@/pages/admin/settings";
import { AdminUsersPage } from "@/pages/admin/users";
import { ChangePasswordPage } from "@/pages/change-password";
import { HomePage } from "@/pages/home";
import { LoginPage } from "@/pages/login";
import { NotFoundPage } from "@/pages/not-found";
import { AttemptReviewPage } from "@/pages/attempt-review";
import { StudentAssignmentsPage } from "@/pages/student-assignments";
import { StudentProgressPage } from "@/pages/student-progress";
import { StudentReferencePage } from "@/pages/student-reference";
import { TeacherGroupsPage } from "@/pages/teacher/groups";
import { TeacherScenarioPage } from "@/pages/teacher/scenario";
import { TeacherScenariosPage } from "@/pages/teacher/scenarios";
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
              { path: "progress", element: <StudentProgressPage /> },
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
          { path: "scenarios", element: <TeacherScenariosPage /> },
          { path: "scenarios/:scenarioId", element: <TeacherScenarioPage /> },
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
          { index: true, element: <AdminUsersPage /> },
          { path: "services", element: <AdminServicesPage /> },
          { path: "health", element: <AdminHealthPage /> },
          { path: "audit", element: <AdminAuditPage /> },
          { path: "backups", element: <AdminBackupsPage /> },
          { path: "settings", element: <AdminSettingsPage /> },
        ],
      },
    ],
  },
  { path: "*", element: <NotFoundPage /> },
]);
