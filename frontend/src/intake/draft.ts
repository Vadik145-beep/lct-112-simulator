import { useCallback, useEffect, useRef, useState } from "react";

import type { AddressIn, CallerIn } from "@/api/intake";

export type Address = Required<AddressIn>;
export type Caller = Required<CallerIn>;

/** The operator-112 card as the interface holds it (PRD 9.3 «SubmittedCard»); every field
 * is present, so the API body (`CardIn`, where they are optional) is always complete. */
export interface Card {
  signs_path: string[];
  incident_type: string | null;
  flags: Record<string, boolean>;
  services: string[];
  address: Address;
  caller: Caller;
  description: string;
}

export const EMPTY_ADDRESS: Address = {
  region: "",
  city: "",
  okrug: "",
  district: "",
  street: "",
  house: "",
  building: "",
  structure: "",
  apartment: "",
  entrance: "",
  floor: "",
  code: "",
  object: "",
  descriptive: "",
};

export const EMPTY_CALLER: Caller = { name: "", role: "", phone: "" };

const MOSCOW = "Москва";

/** One line of the address as the card header shows it. */
export function addressLine(a: Address): string {
  const parts = ["Россия"];
  if (a.region) parts.push(a.region);
  if (a.city || !a.region) parts.push(a.city || MOSCOW);
  if (a.street) parts.push(a.street);
  if (a.house) parts.push(a.house);
  if (a.building) parts.push(`к. ${a.building}`);
  if (a.structure) parts.push(`стр. ${a.structure}`);
  if (a.entrance) parts.push(`под. ${a.entrance}`);
  if (a.object && !a.street) parts.push(a.object);
  const line = parts.join(", ");
  return a.descriptive && !a.street && !a.object ? a.descriptive : line;
}

export const EMPTY_CARD: Card = {
  signs_path: [],
  incident_type: null,
  flags: {},
  services: [],
  address: { ...EMPTY_ADDRESS },
  caller: { ...EMPTY_CALLER },
  description: "",
};

export interface CardDraft {
  card: Card;
  /** Client clock of the last change, ISO; decides between the local and the server copy. */
  updated_at: string;
  /** Services the operator added by hand (auto ones are recomputed from type and flags). */
  manual_services: string[];
  /** Id of the save in flight; a retry after an outage reuses it (idempotent on the server). */
  submission_id: string | null;
}

const KEY_PREFIX = "intake.draft.";
// PRD 11: the draft goes to the server at most once per 2 s.
export const SERVER_SAVE_INTERVAL_MS = 2000;

function normalize(card: Record<string, unknown> | null | undefined): Card {
  const c = (card ?? {}) as Partial<{
    signs_path: string[];
    incident_type: string | null;
    flags: Record<string, boolean>;
    services: string[];
    address: Partial<Address>;
    caller: Partial<Caller>;
    description: string;
  }>;
  return {
    signs_path: c.signs_path ?? [],
    incident_type: c.incident_type ?? null,
    flags: c.flags ?? {},
    services: c.services ?? [],
    address: { ...EMPTY_ADDRESS, ...(c.address ?? {}) },
    caller: { ...EMPTY_CALLER, ...(c.caller ?? {}) },
    description: c.description ?? "",
  };
}

function readLocal(attemptId: string): CardDraft | null {
  try {
    const raw = localStorage.getItem(KEY_PREFIX + attemptId);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<CardDraft>;
    return {
      card: normalize(parsed.card as Record<string, unknown> | undefined),
      updated_at: parsed.updated_at ?? "",
      manual_services: parsed.manual_services ?? [],
      submission_id: parsed.submission_id ?? null,
    };
  } catch {
    return null;
  }
}

function writeLocal(attemptId: string, draft: CardDraft | null) {
  try {
    if (draft) localStorage.setItem(KEY_PREFIX + attemptId, JSON.stringify(draft));
    else localStorage.removeItem(KEY_PREFIX + attemptId);
  } catch {
    // Storage unavailable: the draft lives in memory and on the server.
  }
}

/**
 * Picks the fresher of the local and the server copies of the card (PRD 13.5: a reload in
 * the middle of a call keeps what was typed; another device keeps what the server has).
 */
export function initialDraft(attemptId: string, server: Record<string, unknown> | null | undefined): CardDraft {
  const local = readLocal(attemptId);
  const serverAt = typeof server?.updated_at === "string" ? server.updated_at : "";
  if (server && (!local || serverAt > local.updated_at)) {
    return {
      card: normalize(server),
      updated_at: serverAt,
      manual_services: local?.manual_services ?? [],
      submission_id: local?.submission_id ?? null,
    };
  }
  return local ?? { card: { ...EMPTY_CARD }, updated_at: "", manual_services: [], submission_id: null };
}

export function useCardDraft(
  attemptId: string,
  server: Record<string, unknown> | null | undefined,
  saveToServer: (card: Card, updatedAt: string) => void,
) {
  const [draft, setDraftState] = useState<CardDraft>(() => initialDraft(attemptId, server));
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSent = useRef(0);
  const pending = useRef<CardDraft | null>(null);
  const save = useRef(saveToServer);
  save.current = saveToServer;

  useEffect(() => {
    setDraftState(initialDraft(attemptId, server));
    // The server copy matters only on mount / when the attempt changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attemptId]);

  const flush = useCallback(() => {
    const next = pending.current;
    pending.current = null;
    if (!next) return;
    lastSent.current = Date.now();
    save.current(next.card, next.updated_at);
  }, []);

  const update = useCallback(
    (change: (card: Card, manual: string[]) => { card: Card; manual?: string[] }) => {
      setDraftState((prev) => {
        const result = change(prev.card, prev.manual_services);
        const next: CardDraft = {
          ...prev,
          card: result.card,
          manual_services: result.manual ?? prev.manual_services,
          updated_at: new Date().toISOString(),
        };
        writeLocal(attemptId, next);
        pending.current = next;
        if (timer.current) clearTimeout(timer.current);
        const wait = SERVER_SAVE_INTERVAL_MS - (Date.now() - lastSent.current);
        timer.current = setTimeout(flush, Math.max(0, wait));
        return next;
      });
    },
    [attemptId, flush],
  );

  const setSubmissionId = useCallback(
    (id: string | null) => {
      setDraftState((prev) => {
        const next = { ...prev, submission_id: id };
        writeLocal(attemptId, next);
        return next;
      });
    },
    [attemptId],
  );

  const clear = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    pending.current = null;
    writeLocal(attemptId, null);
  }, [attemptId]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  return { draft, update, setSubmissionId, clear, flush };
}

export function newId(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
