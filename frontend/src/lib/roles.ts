import type { Role } from "@/api/client";

export const ROLE_TITLES: Record<Role, string> = {
  student: "Обучающийся",
  teacher: "Преподаватель",
  admin: "Администратор",
};

export const ROLE_HOME: Record<Role, string> = {
  student: "/student",
  teacher: "/teacher",
  admin: "/admin",
};

/** The reference (memo, materials, classifier) of a role's cabinet; the review links to it
 * from the typical errors, and a teacher has no access to the student's cabinet. */
export const REFERENCE_PATH: Record<Role, string> = {
  student: "/student/reference",
  teacher: "/teacher/reference",
  admin: "/student/reference",
};

export interface NavItem {
  to: string;
  label: string;
}

/** Navigation per role. Items point at sections that later waves fill in. */
export const ROLE_NAV: Record<Role, NavItem[]> = {
  student: [
    { to: "/student", label: "Мои задания" },
    { to: "/student/progress", label: "Прогресс" },
    { to: "/student/reference", label: "Справочник" },
  ],
  teacher: [
    { to: "/teacher", label: "Занятия" },
    { to: "/teacher/groups", label: "Группы" },
    { to: "/teacher/analytics", label: "Аналитика" },
    { to: "/teacher/scenarios", label: "Сценарии" },
  ],
  admin: [
    { to: "/admin", label: "Пользователи" },
    { to: "/admin/services", label: "Службы" },
    { to: "/admin/health", label: "Состояние" },
    { to: "/admin/audit", label: "Аудит" },
    { to: "/admin/backups", label: "Копии" },
    { to: "/admin/settings", label: "Настройки" },
  ],
};
