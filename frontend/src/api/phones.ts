import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { components } from "@/api/schema";
import { attemptKey, unwrap } from "@/api/training";

/** The phone directory of the dispatch services and the virtual phone of the card (#263). */

export type PhoneEntryOut = components["schemas"]["PhoneEntryOut"];
export type PhoneEntryIn = components["schemas"]["PhoneEntryIn"];
export type PhoneEntryPatch = components["schemas"]["PhoneEntryPatch"];
export type PhoneDirectoryOut = components["schemas"]["PhoneDirectoryOut"];
export type PhoneBookOut = components["schemas"]["PhoneBookOut"];
export type PhoneBookEntryOut = components["schemas"]["PhoneBookEntryOut"];

export const phoneDirectoryKey = ["teacher", "phone-directory"] as const;
export const phoneBookKey = (attemptId: string) => ["phone-book", attemptId] as const;

export function usePhoneDirectory() {
  return useQuery({
    queryKey: phoneDirectoryKey,
    queryFn: () => unwrap(api.GET("/api/phone-directory")),
  });
}

function useDirectoryMutation<T>(mutationFn: (arg: T) => Promise<PhoneEntryOut>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => void client.invalidateQueries({ queryKey: phoneDirectoryKey }),
  });
}

export function useAddPhone() {
  return useDirectoryMutation((body: PhoneEntryIn) =>
    unwrap(api.POST("/api/phone-directory", { body })),
  );
}

export function useUpdatePhone() {
  return useDirectoryMutation(({ id, ...body }: PhoneEntryPatch & { id: string }) =>
    unwrap(
      api.PATCH("/api/phone-directory/{entry_id}", { params: { path: { entry_id: id } }, body }),
    ),
  );
}

export function useDeletePhone() {
  return useDirectoryMutation((id: string) =>
    unwrap(api.DELETE("/api/phone-directory/{entry_id}", { params: { path: { entry_id: id } } })),
  );
}

/** The book of the virtual phone on a card: the people of the card's service. */
export function usePhoneBook(attemptId: string) {
  return useQuery({
    queryKey: phoneBookKey(attemptId),
    queryFn: () =>
      unwrap(
        api.GET("/api/attempts/{attempt_id}/phone-book", {
          params: { path: { attempt_id: attemptId } },
        }),
      ),
    staleTime: 60_000,
  });
}

/** Dials a number on the virtual phone; the server tells who answers. */
export function useDial(attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (number: string) =>
      unwrap(
        api.POST("/api/attempts/{attempt_id}/dial", {
          params: { path: { attempt_id: attemptId } },
          body: { number },
        }),
      ),
    onSuccess: (data) => client.setQueryData(attemptKey(attemptId), data.attempt),
  });
}
