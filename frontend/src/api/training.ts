import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";

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

/** Error answered by the server: the HTTP status and the API error code, when the body has one. */
export class RequestError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string | null,
  ) {
    super(message);
    this.name = "RequestError";
  }
}

function errorCode(error: unknown): string | null {
  if (error && typeof error === "object" && "error" in error) {
    const inner = (error as { error?: { code?: string } }).error;
    if (inner?.code) return inner.code;
  }
  return null;
}

export async function unwrap<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response };
  try {
    result = await call;
  } catch {
    throw new NetworkError();
  }
  if (result.data === undefined) {
    throw new RequestError(errorMessage(result.error), result.response.status, errorCode(result.error));
  }
  return result.data;
}

export const journalKey = (sessionId: string) => ["journal", sessionId] as const;
export const attemptKey = (attemptId: string) => ["attempt", attemptId] as const;

// «Мои задания» has no WebSocket of its own: a lesson the teacher starts shows up on
// the next poll.
const ASSIGNMENTS_POLL_MS = 5000;

export function useAssignments() {
  return useQuery({
    queryKey: ["assignments"],
    queryFn: () => unwrap(api.GET("/api/me/assignments")),
    refetchInterval: ASSIGNMENTS_POLL_MS,
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

// PRD 12: «attempt.progress» goes out at most once per 2 s.
const PROGRESS_INTERVAL_MS = 2000;

/**
 * Tells the teacher's monitoring what the trainee is doing in the card. Calls are
 * throttled: the latest stage is sent, at most once per 2 s; failures are ignored.
 */
export function useProgressReporter(attemptId: string) {
  const last = useRef(0);
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);
  const send = useCallback(
    (stage: string) => {
      void api.POST("/api/attempts/{attempt_id}/progress", {
        params: { path: { attempt_id: attemptId } },
        body: { stage },
      });
      last.current = Date.now();
    },
    [attemptId],
  );
  useEffect(
    () => () => {
      if (pending.current) clearTimeout(pending.current);
    },
    [],
  );
  return useCallback(
    (stage: string) => {
      if (pending.current) clearTimeout(pending.current);
      const wait = PROGRESS_INTERVAL_MS - (Date.now() - last.current);
      if (wait <= 0) send(stage);
      else pending.current = setTimeout(() => send(stage), wait);
    },
    [send],
  );
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
