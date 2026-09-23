import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";

import { api, errorMessage } from "@/api/client";
import { getAccessToken } from "@/api/token";
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

export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response };
  try {
    result = await call;
  } catch {
    throw new NetworkError();
  }
  if (result.data === undefined) {
    throw new RequestError(
      errorMessage(result.error),
      result.response.status,
      errorCode(result.error),
    );
  }
  return result.data;
}

export const journalKey = (sessionId: string) =>
  ["journal", sessionId] as const;
export const attemptKey = (attemptId: string) =>
  ["attempt", attemptId] as const;

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
          params: {
            path: { session_id: sessionId },
            query: { page, per_page: perPage },
          },
        }),
      ),
    placeholderData: (previous) => previous,
  });
}

export function useAttempt(attemptId: string) {
  return useQuery({
    queryKey: attemptKey(attemptId),
    queryFn: () =>
      unwrap(
        api.GET("/api/attempts/{attempt_id}", {
          params: { path: { attempt_id: attemptId } },
        }),
      ),
  });
}

export function useOpenAttempt(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/attempts/{attempt_id}/open", {
          params: { path: { attempt_id: attemptId } },
        }),
      ),
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
      void client.invalidateQueries({
        queryKey: journalKey(data.attempt.session.id),
      });
    },
  });
}

export function useFinishAttempt(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/attempts/{attempt_id}/finish", {
          params: { path: { attempt_id: attemptId } },
        }),
      ),
    onSuccess: (data) => {
      client.setQueryData(attemptKey(attemptId), data.attempt);
      void client.invalidateQueries({
        queryKey: journalKey(data.attempt.session.id),
      });
    },
  });
}

export type FlagFieldRequest = components["schemas"]["FlagFieldRequest"];

/** «Отметить ошибку» in a card field (issue #35); a second flag on the field replaces it. */
export function useFlagField(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: FlagFieldRequest) =>
      unwrap(
        api.POST("/api/attempts/{attempt_id}/flag-field", {
          params: { path: { attempt_id: attemptId } },
          body,
        }),
      ),
    onSuccess: (data) =>
      client.setQueryData(attemptKey(attemptId), data.attempt),
  });
}

export function useUnflagField(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (field: string) =>
      unwrap(
        api.DELETE("/api/attempts/{attempt_id}/flag-field", {
          params: { path: { attempt_id: attemptId }, query: { field } },
        }),
      ),
    onSuccess: (data) =>
      client.setQueryData(attemptKey(attemptId), data.attempt),
  });
}

export type ServiceCallResponse = components["schemas"]["ServiceCallResponse"];

/** «Позвонить» a service officer from the card (issue #36). */
export function useStartServiceCall(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (service: string) =>
      unwrap(
        api.POST("/api/attempts/{attempt_id}/service-call", {
          params: { path: { attempt_id: attemptId } },
          body: { service },
        }),
      ),
    onSuccess: (data) =>
      client.setQueryData(attemptKey(attemptId), data.attempt),
  });
}

export function useSayToOfficer(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      callId,
      text,
      actionId,
    }: {
      callId: string;
      text: string;
      actionId: string;
    }) =>
      unwrap(
        api.POST("/api/attempts/{attempt_id}/service-call/{call_id}/say", {
          params: { path: { attempt_id: attemptId, call_id: callId } },
          body: { text, action_id: actionId },
        }),
      ),
    onSuccess: (data) =>
      client.setQueryData(attemptKey(attemptId), data.attempt),
  });
}

/** A recorded phrase of the dispatcher → speech recognition → the officer's reply (no telephony). */
export function useSpeakToOfficer(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      callId,
      blob,
      actionId,
    }: {
      callId: string;
      blob: Blob;
      actionId: string;
    }) => {
      const form = new FormData();
      form.append("file", blob, blob.type.includes("ogg") ? "q.ogg" : "q.webm");
      form.append("action_id", actionId);
      const res = await fetch(
        `/api/attempts/${attemptId}/service-call/${callId}/utterance`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
          body: form,
        },
      );
      const json = (await res.json().catch(() => null)) as
        ServiceCallResponse | { error?: unknown } | null;
      if (!res.ok || !json || !("call" in json)) {
        const failure = json && "error" in json ? json : null;
        throw new RequestError(
          errorMessage(failure),
          res.status,
          errorCode(failure),
        );
      }
      return json;
    },
    onSuccess: (data) =>
      client.setQueryData(attemptKey(attemptId), data.attempt),
  });
}

/** Ключи браузерного звонка в облако для звонка на карточке (issue #59). */
export function useServiceCloudCall(attemptId: string) {
  return useMutation({
    mutationFn: (callId: string) =>
      unwrap(
        api.POST(
          "/api/attempts/{attempt_id}/service-call/{call_id}/cloud-call",
          { params: { path: { attempt_id: attemptId, call_id: callId } } },
        ),
      ),
  });
}

/** «Ответить» on the squad's incoming report (issue #103). */
export function useAnswerServiceCall(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (callId: string) =>
      unwrap(
        api.POST("/api/attempts/{attempt_id}/service-call/{call_id}/answer", {
          params: { path: { attempt_id: attemptId, call_id: callId } },
        }),
      ),
    onSuccess: (data) =>
      client.setQueryData(attemptKey(attemptId), data.attempt),
  });
}

export function useEndServiceCall(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (callId: string) =>
      unwrap(
        api.POST("/api/attempts/{attempt_id}/service-call/{call_id}/end", {
          params: { path: { attempt_id: attemptId, call_id: callId } },
        }),
      ),
    onSuccess: (data) =>
      client.setQueryData(attemptKey(attemptId), data.attempt),
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

/** Methodical materials the trainee can read in full: the memo and the teacher's documents. */
export function useMaterials() {
  return useQuery({
    queryKey: ["reference-materials"],
    queryFn: () => unwrap(api.GET("/api/reference/materials")),
    staleTime: 60_000,
  });
}

export function useMaterial(name: string) {
  return useQuery({
    queryKey: ["reference-material", name],
    queryFn: () => unwrap(api.GET("/api/reference/materials/{name}", { params: { path: { name } } })),
    staleTime: 60_000,
  });
}

export function useReferenceSearch(query: string) {
  const q = query.trim();
  return useQuery({
    queryKey: ["reference-search", q],
    queryFn: () =>
      unwrap(api.GET("/api/reference/search", { params: { query: { q } } })),
    enabled: q.length >= 2,
    placeholderData: (previous) => previous,
  });
}
