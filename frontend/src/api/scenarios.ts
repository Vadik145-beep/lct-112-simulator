import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { components, paths } from "@/api/schema";
import { unwrap } from "@/api/training";

export type ScenarioListItem = components["schemas"]["ScenarioListItem"];
export type ScenarioOut = components["schemas"]["ScenarioOut"];
export type ScenarioOptionsOut = components["schemas"]["ScenarioOptionsOut"];
export type ReplyOut = components["schemas"]["ReplyOut"];
export type VersionOut = components["schemas"]["VersionOut"];
export type GenerateIn = components["schemas"]["GenerateIn"];
export type JobOut = components["schemas"]["JobOut"];
export type GrammarReportOut = components["schemas"]["GrammarReportOut"];
export type GrammarIssueOut = components["schemas"]["GrammarIssueOut"];
export type PreviewOut = components["schemas"]["PreviewOut"];
export type PreviewTurnIn = components["schemas"]["PreviewTurnIn"];
export type ReferenceDocOut = components["schemas"]["ReferenceDocOut"];

export interface ScenarioFilters {
  kind?: "call_intake" | "card_response" | undefined;
  group?: string | undefined;
  source?: string | undefined;
  status?: "draft" | "review" | "approved" | undefined;
  ticket?: string | undefined;
  q?: string | undefined;
}

export const scenarioOptionsKey = ["scenarios", "options"] as const;
export const scenariosKey = (filters: ScenarioFilters) => ["scenarios", "list", filters] as const;
export const scenarioKey = (id: string) => ["scenarios", "one", id] as const;
export const jobKey = (id: string) => ["jobs", id] as const;
export const referenceDocsKey = ["reference", "docs"] as const;

type ListQuery = NonNullable<NonNullable<paths["/api/scenarios"]["get"]["parameters"]["query"]>>;

function clean(filters: ScenarioFilters): ListQuery {
  return Object.fromEntries(Object.entries(filters).filter(([, v]) => v !== undefined && v !== "")) as ListQuery;
}

export function useScenarioOptions() {
  return useQuery({
    queryKey: scenarioOptionsKey,
    queryFn: () => unwrap(api.GET("/api/scenarios/options")),
    staleTime: 60_000,
  });
}

export function useScenarios(filters: ScenarioFilters) {
  const query = clean(filters);
  return useQuery({
    queryKey: scenariosKey(query as ScenarioFilters),
    queryFn: () => unwrap(api.GET("/api/scenarios", { params: { query } })),
  });
}

export function useScenario(id: string, refetchInterval?: number | false) {
  return useQuery({
    queryKey: scenarioKey(id),
    queryFn: () => unwrap(api.GET("/api/scenarios/{scenario_id}", { params: { path: { scenario_id: id } } })),
    refetchInterval: refetchInterval ?? false,
  });
}

/** A background job: polled every second until it ends. */
export function useJob(id: string | null) {
  return useQuery({
    queryKey: jobKey(id ?? ""),
    queryFn: () => unwrap(api.GET("/api/jobs/{job_id}", { params: { path: { job_id: id ?? "" } } })),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "done" || status === "failed" ? false : 1000;
    },
  });
}

function useScenarioMutation<TVars>(id: string, run: (vars: TVars) => Promise<ScenarioOut>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: (data) => {
      client.setQueryData(scenarioKey(id), data);
      void client.invalidateQueries({ queryKey: ["scenarios", "list"] });
    },
  });
}

const path = (scenario_id: string) => ({ params: { path: { scenario_id } } });

export function useUpdateScenario(id: string) {
  return useScenarioMutation<Record<string, unknown>>(id, (body) =>
    unwrap(api.PUT("/api/scenarios/{scenario_id}", { ...path(id), body: { body } })),
  );
}

export function useApproveScenario(id: string) {
  return useScenarioMutation<{ reference?: boolean; replies?: boolean; confirm_grammar?: boolean }>(id, (body) =>
    unwrap(
      api.POST("/api/scenarios/{scenario_id}/approve", {
        ...path(id),
        body: { reference: body.reference ?? true, replies: body.replies ?? true, confirm_grammar: body.confirm_grammar ?? false },
      }),
    ),
  );
}

export function useEditReply(id: string) {
  return useScenarioMutation<{ reply_id: number; text?: string; topic?: string }>(id, ({ reply_id, ...body }) =>
    unwrap(
      api.PUT("/api/scenarios/{scenario_id}/replies/{reply_id}", {
        params: { path: { scenario_id: id, reply_id } },
        body,
      }),
    ),
  );
}

export function useAddReply(id: string) {
  return useScenarioMutation<{ topic: string; text: string }>(id, (body) =>
    unwrap(api.POST("/api/scenarios/{scenario_id}/replies", { ...path(id), body })),
  );
}

export function useDeleteReply(id: string) {
  return useScenarioMutation<number>(id, (reply_id) =>
    unwrap(
      api.DELETE("/api/scenarios/{scenario_id}/replies/{reply_id}", {
        params: { path: { scenario_id: id, reply_id } },
      }),
    ),
  );
}

export function useApproveReplies(id: string) {
  return useScenarioMutation<{ reply_ids: number[] | null; confirm_grammar?: boolean }>(id, (body) =>
    unwrap(
      api.POST("/api/scenarios/{scenario_id}/replies/approve", {
        ...path(id),
        body: { reply_ids: body.reply_ids, confirm_grammar: body.confirm_grammar ?? false },
      }),
    ),
  );
}

export function useUploadReplyAudio(id: string) {
  return useScenarioMutation<{ reply_id: number; file: File }>(id, ({ reply_id, file }) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return unwrap(
      api.POST("/api/scenarios/{scenario_id}/replies/{reply_id}/audio", {
        params: { path: { scenario_id: id, reply_id } },
        // openapi-fetch serializes objects as JSON; a FormData body must pass through as is.
        body: form as unknown as { file: string },
        bodySerializer: (body) => body as unknown as BodyInit,
      }),
    );
  });
}

export function useGenerateScenario() {
  return useMutation({
    mutationFn: (body: GenerateIn) => unwrap(api.POST("/api/scenarios/generate", { body })),
  });
}

export function useReviseScenario(id: string) {
  return useMutation({
    mutationFn: (comment: string) =>
      unwrap(api.POST("/api/scenarios/{scenario_id}/revise", { ...path(id), body: { comment } })),
  });
}

export function useGrammarCheck(id: string) {
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/scenarios/{scenario_id}/grammar", path(id))),
  });
}

export function usePreviewDialog(id: string) {
  return useMutation({
    mutationFn: (body: { text: string; history: PreviewTurnIn[] }) =>
      unwrap(api.POST("/api/scenarios/{scenario_id}/preview-dialog", { ...path(id), body })),
  });
}

export function useReferenceDocs() {
  return useQuery({ queryKey: referenceDocsKey, queryFn: () => unwrap(api.GET("/api/reference/docs")) });
}

export function useUploadReferenceDoc() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file, file.name);
      return unwrap(
        api.POST("/api/reference/docs", {
          body: form as unknown as { file: string },
          bodySerializer: (body) => body as unknown as BodyInit,
        }),
      );
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: referenceDocsKey }),
  });
}

export function useDeleteReferenceDoc() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => unwrap(api.DELETE("/api/reference/docs/{name}", { params: { path: { name } } })),
    onSuccess: () => void client.invalidateQueries({ queryKey: referenceDocsKey }),
  });
}

/** The trainee's saved card of a call-intake attempt → a card_response draft (source=student). */
export function useScenarioFromAttempt() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (attemptId: string) =>
      unwrap(api.POST("/api/scenarios/from-attempt/{attempt_id}", { params: { path: { attempt_id: attemptId } } })),
    onSuccess: (data) => {
      client.setQueryData(scenarioKey(data.id), data);
      void client.invalidateQueries({ queryKey: ["scenarios", "list"] });
    },
  });
}
