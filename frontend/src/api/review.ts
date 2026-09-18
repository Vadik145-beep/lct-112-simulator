import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { components } from "@/api/schema";
import { reportKey } from "@/api/teacher";
import { getAccessToken } from "@/api/token";
import { attemptKey, unwrap } from "@/api/training";

export type OverrideOut = components["schemas"]["OverrideOut"];
export type CommentOut = components["schemas"]["CommentOut"];
export type OverrideIn = components["schemas"]["OverrideIn"];
export type ProgressOut = components["schemas"]["ProgressOut"];

/** PATCH /attempts/{id}/evaluation: the teacher's total with a reason; refreshes the review
 * and the report of the session. */
export function useOverrideEvaluation(attemptId: string, sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: OverrideIn) =>
      unwrap(api.PATCH("/api/attempts/{attempt_id}/evaluation", { params: { path: { attempt_id: attemptId } }, body })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: attemptKey(attemptId) });
      void client.invalidateQueries({ queryKey: reportKey(sessionId) });
    },
  });
}

export function useAddComment(attemptId: string, sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (text: string) =>
      unwrap(api.POST("/api/attempts/{attempt_id}/comments", { params: { path: { attempt_id: attemptId } }, body: { text } })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: attemptKey(attemptId) });
      void client.invalidateQueries({ queryKey: reportKey(sessionId) });
    },
  });
}

export function useProgress() {
  return useQuery({ queryKey: ["progress"], queryFn: () => unwrap(api.GET("/api/me/progress")) });
}

export type ExportFormat = "pdf" | "xlsx" | "csv";

/** Downloads the report file: the endpoint needs the bearer token, which a plain link cannot
 * send, so the file is fetched and handed to the browser as a blob. */
export async function downloadReport(sessionId: string, format: ExportFormat): Promise<void> {
  const res = await fetch(`/api/sessions/${sessionId}/report.${format}`, {
    headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
  });
  if (!res.ok) {
    let message = `Не удалось получить отчёт (${res.status}).`;
    try {
      const body = (await res.json()) as { error?: { message?: string } };
      if (body.error?.message) message = body.error.message;
    } catch {
      // not JSON: keep the generic message
    }
    throw new Error(message);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `otchet-${sessionId.slice(0, 8)}.${format}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
