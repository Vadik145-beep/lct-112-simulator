import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { components } from "@/api/schema";
import { unwrap } from "@/api/training";

export type SipAccountOut = components["schemas"]["SipAccountOut"];
export type CurrentCallOut = components["schemas"]["CurrentCallOut"];
export type DialogOut = components["schemas"]["DialogOut"];
export type DialogTurnOut = components["schemas"]["DialogTurnOut"];
export type TurnResponse = components["schemas"]["TurnResponse"];
export type AnswerResponse = components["schemas"]["AnswerResponse"];
export type CallResponse = components["schemas"]["CallResponse"];
export type WebCallOut = components["schemas"]["WebCallOut"];

export type UserOut = components["schemas"]["UserOut"];

/** The trainee's profile with his own phone (docs/MULTIFON.md). */
export function useMyProfile(enabled = true) {
  return useQuery({
    queryKey: ["me", "profile"],
    queryFn: () => unwrap(api.GET("/api/me")),
    enabled,
  });
}

/** Saves the trainee's own phone for lessons with calls to the phone; "" removes it. */
export function useSetMyPhone() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (phone: string) => unwrap(api.PUT("/api/me/phone", { body: { phone } })),
    onSuccess: (data) => client.setQueryData(["me", "profile"], data),
  });
}

/** Credentials of the softphone (PRD 9.5); `enabled: false` = no Asterisk on this stand. */
export function useSipAccount(enabled: boolean) {
  return useQuery({
    queryKey: ["me", "sip"],
    queryFn: () => unwrap(api.GET("/api/me/sip")),
    enabled,
    staleTime: Infinity,
    retry: 1,
  });
}

// Without telephony the panel learns about a new call by polling.
export const CURRENT_CALL_POLL_MS = 3000;

export function useCurrentCall(enabled: boolean, pollMs = CURRENT_CALL_POLL_MS) {
  return useQuery({
    queryKey: ["me", "call"],
    queryFn: () => unwrap(api.GET("/api/me/call")),
    enabled,
    refetchInterval: pollMs,
  });
}

const path = (attemptId: string) => ({ params: { path: { attempt_id: attemptId } } });

export const telephonyApi = {
  answer: (attemptId: string) => unwrap(api.POST("/api/attempts/{attempt_id}/answer", path(attemptId))),
  hangup: (attemptId: string) => unwrap(api.POST("/api/attempts/{attempt_id}/hangup", path(attemptId))),
  noContact: (attemptId: string) =>
    unwrap(api.POST("/api/attempts/{attempt_id}/no-contact", path(attemptId))),
  callDropped: (attemptId: string) =>
    unwrap(api.POST("/api/attempts/{attempt_id}/call-dropped", path(attemptId))),
  dialog: (attemptId: string) => unwrap(api.GET("/api/attempts/{attempt_id}/dialog", path(attemptId))),
  say: (attemptId: string, text: string, actionId: string) =>
    unwrap(
      api.POST("/api/attempts/{attempt_id}/say", {
        ...path(attemptId),
        body: { text, action_id: actionId },
      }),
    ),
  /** Keys of a browser call to the cloud caller (plan/track-c-vapi.md, no telephony). */
  cloudCall: (attemptId: string) =>
    unwrap(api.POST("/api/attempts/{attempt_id}/cloud-call", path(attemptId))),
  cloudCallFailed: (attemptId: string, reason: string) =>
    unwrap(
      api.POST("/api/attempts/{attempt_id}/cloud-call/failed", {
        ...path(attemptId),
        body: { reason },
      }),
    ),
  /** A recorded phrase (WebM/Opus from MediaRecorder) → speech recognition → the caller's reply. */
  utterance: async (attemptId: string, blob: Blob, actionId: string): Promise<TurnResponse> => {
    const form = new FormData();
    form.append("file", blob, "utterance.webm");
    form.append("action_id", actionId);
    return unwrap(
      api.POST("/api/attempts/{attempt_id}/utterance", {
        ...path(attemptId),
        // openapi-fetch serialises FormData as-is when told not to touch the body.
        body: form as unknown as { file: string; action_id?: string | null },
        bodySerializer: (body: unknown) => body as FormData,
      }),
    );
  },
};
