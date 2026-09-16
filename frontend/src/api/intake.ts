import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, errorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { attemptKey, journalKey, unwrap } from "@/api/training";
import { getAccessToken } from "@/api/token";

export type DialogOut = components["schemas"]["DialogOut"];
export type DialogTurnOut = components["schemas"]["DialogTurnOut"];
export type TurnResponse = components["schemas"]["TurnResponse"];
export type CardIn = components["schemas"]["CardIn"];
export type AddressIn = components["schemas"]["AddressIn"];
export type CallerIn = components["schemas"]["CallerIn"];
export type SubmitResponse = components["schemas"]["SubmitResponse"];
export type IntakeOut = components["schemas"]["IntakeOut"];
export type TypeServicesOut = components["schemas"]["TypeServicesOut"];
export type IncidentFlagOut = components["schemas"]["IncidentFlagOut"];
export type StreetOut = components["schemas"]["StreetOut"];
export type ClassifierNode = components["schemas"]["ClassifierNode"];
export type ClassifierGroupOut = components["schemas"]["ClassifierGroupOut"];

/**
 * State of the call as the telephony wave reports it in `DialogOut.call` (PRD 9.5). This
 * wave reads the field when it is there and falls back to the transcript otherwise, so the
 * panel works the same before and after the softphone lands.
 */
export type CallState = "idle" | "ringing" | "answered" | "ended";
export interface CallInfo {
  state: CallState;
  end_reason: string | null;
  ended_at: string | null;
  call_dropped_marked: boolean;
  no_contact_marked: boolean;
  telephony: boolean;
  recording_available: boolean;
}

export function callInfo(dialog: DialogOut | undefined, closed: boolean): CallInfo {
  const raw = (dialog as (DialogOut & { call?: Partial<CallInfo> }) | undefined)?.call;
  const fallback: CallState = closed ? "ended" : dialog?.answered_at ? "answered" : "ringing";
  return {
    state: (raw?.state as CallState | undefined) ?? fallback,
    end_reason: raw?.end_reason ?? null,
    ended_at: raw?.ended_at ?? null,
    call_dropped_marked: raw?.call_dropped_marked ?? false,
    no_contact_marked: raw?.no_contact_marked ?? false,
    telephony: raw?.telephony ?? false,
    recording_available: raw?.recording_available ?? false,
  };
}

export const dialogKey = (attemptId: string) => ["dialog", attemptId] as const;

export function useDialog(attemptId: string, enabled = true) {
  return useQuery({
    queryKey: dialogKey(attemptId),
    queryFn: () =>
      unwrap(api.GET("/api/attempts/{attempt_id}/dialog", { params: { path: { attempt_id: attemptId } } })),
    enabled,
  });
}

function useTurnMutation<TVars>(attemptId: string, call: (vars: TVars) => Promise<TurnResponse>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: call,
    onSuccess: (data) => client.setQueryData(dialogKey(attemptId), data.dialog),
  });
}

export function useSay(attemptId: string) {
  return useTurnMutation(attemptId, (body: { text: string; action_id: string }) =>
    unwrap(api.POST("/api/attempts/{attempt_id}/say", { params: { path: { attempt_id: attemptId } }, body })),
  );
}

export function useAskTopic(attemptId: string) {
  return useTurnMutation(attemptId, (body: { topic: string; action_id: string }) =>
    unwrap(
      api.POST("/api/attempts/{attempt_id}/ask-topic", { params: { path: { attempt_id: attemptId } }, body }),
    ),
  );
}

/** A recorded phrase of the operator → speech recognition → the caller's reply. */
export function useUtterance(attemptId: string) {
  return useTurnMutation(attemptId, async ({ blob, action_id }: { blob: Blob; action_id: string }) => {
    const form = new FormData();
    form.append("file", blob, blob.type.includes("ogg") ? "q.ogg" : "q.webm");
    form.append("action_id", action_id);
    const res = await fetch(`/api/attempts/${attemptId}/utterance`, {
      method: "POST",
      headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
      body: form,
    });
    const json = (await res.json().catch(() => null)) as TurnResponse | { error?: unknown } | null;
    if (!res.ok || !json || !("dialog" in json)) throw new Error(errorMessage(json && "error" in json ? json : null));
    return json;
  });
}

/**
 * Call controls of the panel: answer, hang up, «нет контакта», «срыв звонка». The routes
 * belong to the telephony wave; until it lands the server answers 404 and the panel keeps
 * a local state, so the browser path still works.
 */
async function callControl(attemptId: string, action: "answer" | "hangup" | "no-contact" | "call-dropped") {
  const res = await fetch(`/api/attempts/${attemptId}/${action}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
  });
  if (res.status === 404) return { supported: false as const, dialog: null };
  const json = (await res.json().catch(() => null)) as { dialog?: DialogOut; error?: unknown } | null;
  if (!res.ok || !json?.dialog) throw new Error(errorMessage(json));
  return { supported: true as const, dialog: json.dialog };
}

export function useCallControl(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (action: "answer" | "hangup" | "no-contact" | "call-dropped") => callControl(attemptId, action),
    onSuccess: (data) => {
      if (data.dialog) client.setQueryData(dialogKey(attemptId), data.dialog);
      else void client.invalidateQueries({ queryKey: dialogKey(attemptId) });
    },
  });
}

export function useSaveDraft(attemptId: string) {
  return useMutation({
    mutationFn: (body: { card: CardIn; updated_at: string }) =>
      unwrap(api.PUT("/api/attempts/{attempt_id}/draft", { params: { path: { attempt_id: attemptId } }, body })),
  });
}

export function useSubmitCard(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { card: CardIn; client_submission_id: string }) =>
      unwrap(api.POST("/api/attempts/{attempt_id}/submit", { params: { path: { attempt_id: attemptId } }, body })),
    onSuccess: (data) => {
      client.setQueryData(attemptKey(attemptId), data.attempt);
      void client.invalidateQueries({ queryKey: journalKey(data.attempt.session.id) });
      void client.invalidateQueries({ queryKey: dialogKey(attemptId) });
    },
  });
}

// ---------------------------------------------------------------- reference data

export function useIncidentFlags() {
  return useQuery({
    queryKey: ["reference", "incident-flags"],
    queryFn: () => unwrap(api.GET("/api/incident-flags")),
    staleTime: Infinity,
  });
}

/** Services АРМ-112 notifies for a type under the chosen flags (PRD 8.5). */
export function useTypeServices(typeCode: string | null, flags: string[]) {
  const key = [...flags].sort().join(",");
  return useQuery({
    queryKey: ["reference", "type-services", typeCode, key],
    queryFn: () =>
      unwrap(
        api.GET("/api/classifier/{code}/services", {
          params: { path: { code: typeCode ?? "" }, query: { flags: key } },
        }),
      ),
    enabled: Boolean(typeCode),
    staleTime: Infinity,
    placeholderData: (previous) => previous,
  });
}

export function useStreets(query: string) {
  const q = query.trim();
  return useQuery({
    queryKey: ["reference", "streets", q],
    queryFn: () => unwrap(api.GET("/api/streets", { params: { query: { q } } })),
    enabled: q.length >= 2,
    staleTime: 60_000,
    placeholderData: (previous) => previous,
  });
}
