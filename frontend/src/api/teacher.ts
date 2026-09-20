import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { components } from "@/api/schema";
import { unwrap } from "@/api/training";

export type SessionListItem = components["schemas"]["SessionListItem"];
export type SessionOut = components["schemas"]["SessionOut"];
export type SessionIn = components["schemas"]["SessionIn"];
export type SessionPatch = components["schemas"]["SessionPatch"];
export type GroupOut = components["schemas"]["GroupOut"];
export type GroupIn = components["schemas"]["GroupIn"];
export type GroupPatch = components["schemas"]["GroupPatch"];
export type StudentOut = components["schemas"]["StudentOut"];
export type MonitorOut = components["schemas"]["MonitorOut"];
export type MonitorStudent = components["schemas"]["MonitorStudent"];
export type MonitorCard = components["schemas"]["MonitorCard"];
export type ReportOut = components["schemas"]["ReportOut"];
export type ReportStudent = components["schemas"]["ReportStudent"];
export type ReportAttempt = components["schemas"]["ReportAttempt"];
export type ClassifierTreeOut = components["schemas"]["ClassifierTreeOut"];
export type ServiceOut = components["schemas"]["ServiceOut"];

export const sessionsKey = ["teacher", "sessions"] as const;
export const sessionKey = (id: string) => ["teacher", "session", id] as const;
export const monitorKey = (id: string) => ["teacher", "monitor", id] as const;
export const reportKey = (id: string) => ["teacher", "report", id] as const;
export const groupsKey = ["teacher", "groups"] as const;

export function useTeacherSessions() {
  return useQuery({ queryKey: sessionsKey, queryFn: () => unwrap(api.GET("/api/sessions")) });
}

export function useTeacherSession(id: string) {
  return useQuery({
    queryKey: sessionKey(id),
    queryFn: () => unwrap(api.GET("/api/sessions/{session_id}", { params: { path: { session_id: id } } })),
  });
}

function useSessionMutation<TVars>(id: string | undefined, run: (vars: TVars) => Promise<SessionOut>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: (data) => {
      client.setQueryData(sessionKey(data.id), data);
      void client.invalidateQueries({ queryKey: sessionsKey });
      if (id) void client.invalidateQueries({ queryKey: monitorKey(id) });
    },
  });
}

export function useCreateSession() {
  return useSessionMutation<SessionIn>(undefined, (body) => unwrap(api.POST("/api/sessions", { body })));
}

export function useUpdateSession(id: string) {
  return useSessionMutation<SessionPatch>(id, (body) =>
    unwrap(api.PATCH("/api/sessions/{session_id}", { params: { path: { session_id: id } }, body })),
  );
}

export function useStartSession(id: string) {
  return useSessionMutation<void>(id, () =>
    unwrap(api.POST("/api/sessions/{session_id}/start", { params: { path: { session_id: id } } })),
  );
}

export function useFinishSession(id: string) {
  const client = useQueryClient();
  const mutation = useSessionMutation<void>(id, () =>
    unwrap(api.POST("/api/sessions/{session_id}/finish", { params: { path: { session_id: id } } })),
  );
  return {
    ...mutation,
    mutate: (vars: void, opts?: Parameters<typeof mutation.mutate>[1]) =>
      mutation.mutate(vars, {
        ...opts,
        onSuccess: (...args) => {
          void client.invalidateQueries({ queryKey: reportKey(id) });
          opts?.onSuccess?.(...args);
        },
      }),
  };
}

export function useMonitor(id: string, enabled = true) {
  return useQuery({
    queryKey: monitorKey(id),
    queryFn: () =>
      unwrap(api.GET("/api/sessions/{session_id}/monitor", { params: { path: { session_id: id } } })),
    enabled,
  });
}

export function useReport(id: string, enabled = true) {
  return useQuery({
    queryKey: reportKey(id),
    queryFn: () =>
      unwrap(api.GET("/api/sessions/{session_id}/report", { params: { path: { session_id: id } } })),
    enabled,
  });
}

export function useGroups() {
  return useQuery({ queryKey: groupsKey, queryFn: () => unwrap(api.GET("/api/groups")) });
}

export type QueuePreviewIn = components["schemas"]["QueuePreviewIn"];
export type QueuePreviewOut = components["schemas"]["QueuePreviewOut"];

/** Cards the form's filters would put into the queue — shown before the lesson is created. */
export function useQueuePreview(body: QueuePreviewIn) {
  return useQuery({
    queryKey: ["teacher", "queue-preview", body],
    queryFn: () => unwrap(api.POST("/api/sessions/preview", { body })),
    placeholderData: (prev) => prev,
  });
}

export type SessionGenerateIn = components["schemas"]["SessionGenerateIn"];
export type SessionGenerateOut = components["schemas"]["SessionGenerateOut"];

/** Drafts scenarios under the lesson's groups, difficulty and services (ТЗ «Настройка учебной
 * среды»); the job is polled with `useJob`, approved drafts enter the queue by the usual filters. */
export function useGenerateForSession(id: string) {
  return useMutation({
    mutationFn: (body: SessionGenerateIn) =>
      unwrap(api.POST("/api/sessions/{session_id}/generate", { params: { path: { session_id: id } }, body })),
  });
}

/** Which AI services answer now: the lesson form warns before a mode silently degrades. */
export function useModels(enabled = true) {
  return useQuery({ queryKey: ["teacher", "models"], queryFn: () => unwrap(api.GET("/api/models")), enabled, staleTime: 15_000 });
}

export function useStudents() {
  return useQuery({ queryKey: ["teacher", "students"], queryFn: () => unwrap(api.GET("/api/students")) });
}

export function useCreateGroup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: GroupIn) => unwrap(api.POST("/api/groups", { body })),
    onSuccess: () => void client.invalidateQueries({ queryKey: groupsKey }),
  });
}

export function useUpdateGroup(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: GroupPatch) =>
      unwrap(api.PATCH("/api/groups/{group_id}", { params: { path: { group_id: id } }, body })),
    onSuccess: () => void client.invalidateQueries({ queryKey: groupsKey }),
  });
}

export function useClassifierTree() {
  return useQuery({
    queryKey: ["reference", "classifier-tree"],
    queryFn: () => unwrap(api.GET("/api/classifier/tree")),
    staleTime: Infinity,
  });
}

export function useServices() {
  return useQuery({
    queryKey: ["reference", "services"],
    queryFn: () => unwrap(api.GET("/api/services")),
    staleTime: Infinity,
  });
}
