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
