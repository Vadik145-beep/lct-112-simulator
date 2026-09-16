import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, errorMessage } from "@/api/client";
import type { components } from "@/api/schema";

export type AssignmentOut = components["schemas"]["AssignmentOut"];
export type JournalOut = components["schemas"]["JournalOut"];
export type JournalItem = components["schemas"]["JournalItem"];
export type AttemptOut = components["schemas"]["AttemptOut"];
export type StatusRequest = components["schemas"]["StatusRequest"];
export type StatusResponse = components["schemas"]["StatusResponse"];
export type TransitionOut = components["schemas"]["TransitionOut"];
export type ServiceStatusOut = components["schemas"]["ServiceStatusOut"];
export type ReferenceSearchOut = components["schemas"]["ReferenceSearchOut"];

/** Error raised for a request that never reached the server (offline, aborted). */
export class NetworkError extends Error {
  constructor() {
    super("Нет связи с сервером. Проверьте сеть и повторите действие.");
    this.name = "NetworkError";
  }
}

async function unwrap<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response };
  try {
    result = await call;
  } catch {
    throw new NetworkError();
  }
  if (result.data === undefined) throw new Error(errorMessage(result.error));
  return result.data;
}

export const journalKey = (sessionId: string) => ["journal", sessionId] as const;
export const attemptKey = (attemptId: string) => ["attempt", attemptId] as const;

export function useAssignments() {
  return useQuery({
    queryKey: ["assignments"],
    queryFn: () => unwrap(api.GET("/api/me/assignments")),
  });
}

export function useJournal(sessionId: string, page: number, perPage: number) {
  return useQuery({
    queryKey: [...journalKey(sessionId), page, perPage],
    queryFn: () =>
      unwrap(
        api.GET("/api/sessions/{session_id}/journal", {
          params: { path: { session_id: sessionId }, query: { page, per_page: perPage } },
        }),
      ),
    placeholderData: (previous) => previous,
  });
}

export function useAttempt(attemptId: string) {
  return useQuery({
    queryKey: attemptKey(attemptId),
    queryFn: () =>
      unwrap(api.GET("/api/attempts/{attempt_id}", { params: { path: { attempt_id: attemptId } } })),
  });
}

export function useOpenAttempt(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/attempts/{attempt_id}/open", { params: { path: { attempt_id: attemptId } } })),
    onSuccess: (data) => client.setQueryData(attemptKey(attemptId), data),
  });
}

export function useSetStatus(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: StatusRequest) =>
      unwrap(
        api.POST("/api/attempts/{attempt_id}/status", {
          params: { path: { attempt_id: attemptId } },
          body,
        }),
      ),
    onSuccess: (data) => {
      client.setQueryData(attemptKey(attemptId), data.attempt);
      void client.invalidateQueries({ queryKey: journalKey(data.attempt.session.id) });
    },
  });
}

export function useFinishAttempt(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/attempts/{attempt_id}/finish", { params: { path: { attempt_id: attemptId } } }),
      ),
    onSuccess: (data) => {
      client.setQueryData(attemptKey(attemptId), data.attempt);
      void client.invalidateQueries({ queryKey: journalKey(data.attempt.session.id) });
    },
  });
}

export function useReferenceSearch(query: string) {
  const q = query.trim();
  return useQuery({
    queryKey: ["reference-search", q],
    queryFn: () => unwrap(api.GET("/api/reference/search", { params: { query: { q } } })),
    enabled: q.length >= 2,
    placeholderData: (previous) => previous,
  });
}
