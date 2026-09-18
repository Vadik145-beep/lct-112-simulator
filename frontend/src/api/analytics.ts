import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { components } from "@/api/schema";
import { unwrap } from "@/api/training";

export type GroupAnalyticsOut = components["schemas"]["GroupAnalyticsOut"];
export type HeatCellOut = components["schemas"]["HeatCellOut"];
export type WeekPointOut = components["schemas"]["WeekPointOut"];
export type ReadinessOut = components["schemas"]["ReadinessOut"];
export type ReadinessModelOut = components["schemas"]["ReadinessModelOut"];

export const groupAnalyticsKey = (groupId: string, days: number) => ["analytics", "group", groupId, days] as const;
export const readinessModelKey = ["analytics", "readiness-model"] as const;

export function useGroupAnalytics(groupId: string | undefined, days: number) {
  return useQuery({
    queryKey: groupAnalyticsKey(groupId ?? "", days),
    enabled: Boolean(groupId),
    queryFn: () =>
      unwrap(
        api.GET("/api/analytics/groups/{group_id}", {
          params: { path: { group_id: groupId ?? "" }, query: { days } },
        }),
      ),
  });
}

export function useReadinessModel() {
  return useQuery({
    queryKey: readinessModelKey,
    queryFn: () => unwrap(api.GET("/api/analytics/readiness-model")),
  });
}
