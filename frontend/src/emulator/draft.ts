import { useCallback, useEffect, useState } from "react";

/** What the status editor holds; saved to localStorage so a reload keeps the comment. */
export interface StatusDraft {
  open: boolean;
  status: string;
  order_number: string;
  comment: string;
  reject_reason: string;
  /** Id of the action in flight; a retry after an outage reuses it (idempotent on the server). */
  action_id: string | null;
}

export const EMPTY_DRAFT: StatusDraft = {
  open: false,
  status: "",
  order_number: "",
  comment: "",
  reject_reason: "",
  action_id: null,
};

const KEY_PREFIX = "dds.draft.";

function read(attemptId: string): StatusDraft {
  try {
    const raw = localStorage.getItem(KEY_PREFIX + attemptId);
    return raw ? { ...EMPTY_DRAFT, ...(JSON.parse(raw) as Partial<StatusDraft>) } : EMPTY_DRAFT;
  } catch {
    return EMPTY_DRAFT;
  }
}

export function useDraft(attemptId: string) {
  const [draft, setDraftState] = useState<StatusDraft>(() => read(attemptId));

  useEffect(() => {
    setDraftState(read(attemptId));
  }, [attemptId]);

  const setDraft = useCallback(
    (update: Partial<StatusDraft> | ((prev: StatusDraft) => StatusDraft)) => {
      setDraftState((prev) => {
        const next = typeof update === "function" ? update(prev) : { ...prev, ...update };
        try {
          if (next.open || next.comment || next.order_number) {
            localStorage.setItem(KEY_PREFIX + attemptId, JSON.stringify(next));
          } else {
            localStorage.removeItem(KEY_PREFIX + attemptId);
          }
        } catch {
          // Storage unavailable: the draft lives only in memory.
        }
        return next;
      });
    },
    [attemptId],
  );

  const clearDraft = useCallback(() => {
    try {
      localStorage.removeItem(KEY_PREFIX + attemptId);
    } catch {
      // Nothing to clear.
    }
    setDraftState(EMPTY_DRAFT);
  }, [attemptId]);

  return { draft, setDraft, clearDraft };
}

export function newActionId(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
