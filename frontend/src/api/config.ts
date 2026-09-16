import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";

/** Public settings of the stand (`GET /api/config`): demo buttons, external AI flag, dialog mode. */
export function usePublicConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/config");
      if (!data) throw error;
      return data;
    },
    staleTime: Infinity,
  });
}
