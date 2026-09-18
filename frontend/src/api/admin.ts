import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { components } from "@/api/schema";
import { unwrap } from "@/api/training";

export type AdminUserOut = components["schemas"]["AdminUserOut"];
export type AdminUserIn = components["schemas"]["AdminUserIn"];
export type AdminUserPatch = components["schemas"]["AdminUserPatch"];
export type AdminUserCreated = components["schemas"]["AdminUserCreated"];
export type AdminServiceOut = components["schemas"]["AdminServiceOut"];
export type AdminServicePatch = components["schemas"]["AdminServicePatch"];
export type HealthOut = components["schemas"]["HealthOut"];
export type ServiceTile = components["schemas"]["ServiceTile"];
export type NotificationOut = components["schemas"]["NotificationOut"];
export type AuditPage = components["schemas"]["AuditPage"];
export type AuditRow = components["schemas"]["AuditRow"];
export type AuditVerifyOut = components["schemas"]["AuditVerifyOut"];
export type BackupsOut = components["schemas"]["BackupsOut"];
export type BackupOut = components["schemas"]["BackupOut"];
export type AdminSettingsOut = components["schemas"]["AdminSettingsOut"];
export type AdminSettingsPatch = components["schemas"]["AdminSettingsPatch"];

export const adminUsersKey = ["admin", "users"] as const;
export const adminServicesKey = ["admin", "services"] as const;
export const adminHealthKey = ["admin", "health"] as const;
export const adminNotificationsKey = ["admin", "notifications"] as const;
export const adminBackupsKey = ["admin", "backups"] as const;
export const adminSettingsKey = ["admin", "settings"] as const;

// «Состояние системы» and the backups refresh themselves while the page is open.
const HEALTH_POLL_MS = 15_000;
const BACKUPS_POLL_MS = 5_000;

// ---------------------------------------------------------------- users

export function useAdminUsers(filters: { role?: string; q?: string } = {}) {
  return useQuery({
    queryKey: [...adminUsersKey, filters],
    queryFn: () =>
      unwrap(
        api.GET("/api/admin/users", {
          params: {
            query: {
              role: (filters.role || null) as AdminUserOut["role"] | null,
              q: filters.q || null,
            },
          },
        }),
      ),
  });
}

function useAdminUserMutation<TVars, TOut>(run: (vars: TVars) => Promise<TOut>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => void client.invalidateQueries({ queryKey: adminUsersKey }),
  });
}

export function useCreateUser() {
  return useAdminUserMutation((body: AdminUserIn) => unwrap(api.POST("/api/admin/users", { body })));
}

export function useUpdateUser() {
  return useAdminUserMutation(({ id, ...body }: Partial<AdminUserPatch> & { id: string }) =>
    unwrap(
      api.PATCH("/api/admin/users/{user_id}", {
        params: { path: { user_id: id } },
        body: { clear_service: false, ...body },
      }),
    ),
  );
}

export function useRevealUser() {
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/api/admin/users/{user_id}/reveal", { params: { path: { user_id: id } } })),
  });
}

export function useResetPassword() {
  return useAdminUserMutation((id: string) =>
    unwrap(api.POST("/api/admin/users/{user_id}/reset-password", { params: { path: { user_id: id } } })),
  );
}

export function useSipAccount() {
  return useAdminUserMutation((id: string) =>
    unwrap(api.POST("/api/admin/users/{user_id}/sip", { params: { path: { user_id: id } } })),
  );
}

// ---------------------------------------------------------------- services

export function useAdminServices() {
  return useQuery({ queryKey: adminServicesKey, queryFn: () => unwrap(api.GET("/api/admin/services")) });
}

export function useUpdateService() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ code, ...body }: AdminServicePatch & { code: string }) =>
      unwrap(api.PATCH("/api/admin/services/{code}", { params: { path: { code } }, body })),
    onSuccess: () => void client.invalidateQueries({ queryKey: adminServicesKey }),
  });
}

// ---------------------------------------------------------------- health

export function useAdminHealth() {
  return useQuery({
    queryKey: adminHealthKey,
    queryFn: () => unwrap(api.GET("/api/admin/health")),
    refetchInterval: HEALTH_POLL_MS,
  });
}

export function useNotifications() {
  return useQuery({
    queryKey: adminNotificationsKey,
    queryFn: () => unwrap(api.GET("/api/admin/notifications")),
    refetchInterval: HEALTH_POLL_MS,
  });
}

export function useAcknowledge() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/admin/notifications/{notification_id}/ack", {
          params: { path: { notification_id: id } },
        }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: adminNotificationsKey });
      void client.invalidateQueries({ queryKey: adminHealthKey });
    },
  });
}

// ---------------------------------------------------------------- audit

export interface AuditFilters {
  actor_login?: string | undefined;
  action?: string | undefined;
  date_from?: string | undefined;
  date_to?: string | undefined;
  page: number;
  per_page: number;
}

export function useAudit(filters: AuditFilters) {
  return useQuery({
    queryKey: ["admin", "audit", filters],
    queryFn: () =>
      unwrap(
        api.GET("/api/admin/audit", {
          params: {
            query: {
              actor_login: filters.actor_login || null,
              action: filters.action || null,
              date_from: filters.date_from || null,
              date_to: filters.date_to || null,
              page: filters.page,
              per_page: filters.per_page,
            },
          },
        }),
      ),
    placeholderData: (previous) => previous,
  });
}

export function useVerifyAudit() {
  return useMutation({ mutationFn: () => unwrap(api.POST("/api/admin/audit/verify")) });
}

// ---------------------------------------------------------------- backups

export function useBackups() {
  return useQuery({
    queryKey: adminBackupsKey,
    queryFn: () => unwrap(api.GET("/api/admin/backups")),
    refetchInterval: BACKUPS_POLL_MS,
  });
}

export function useRequestBackup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/admin/backups")),
    onSuccess: () => void client.invalidateQueries({ queryKey: adminBackupsKey }),
  });
}

// ---------------------------------------------------------------- settings

export function useAdminSettings() {
  return useQuery({ queryKey: adminSettingsKey, queryFn: () => unwrap(api.GET("/api/admin/settings")) });
}

export function useUpdateSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: AdminSettingsPatch) => unwrap(api.PATCH("/api/admin/settings", { body })),
    onSuccess: (data) => client.setQueryData(adminSettingsKey, data),
  });
}
