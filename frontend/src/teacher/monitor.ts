import type { MonitorCard, MonitorOut, MonitorStudent } from "@/api/teacher";
import type { SessionEvent } from "@/emulator/ws";

/** What a trainee is doing in an open card, from `attempt.progress`. */
export interface Stage {
  stage: string;
  at: string;
  /** The card the trainee is in, when the event says so. */
  attempt_id: string | null;
}

export interface MonitorState {
  snapshot: MonitorOut;
  stages: Record<string, Stage>;
  /** Attempts that arrived by event and have no incident title yet (needs a refetch). */
  untitled: string[];
}

export const STAGE_TITLES: Record<string, string> = {
  viewing: "смотрит карточку",
  editing_status: "проставляет статус",
  checking_data: "проверяет данные карточки",
  talking: "говорит с заявителем",
  filling_card: "заполняет карточку 112",
  ringing: "входящий вызов",
  call_ended: "звонок завершён, заполняет карточку",
};

export function initialState(snapshot: MonitorOut): MonitorState {
  return { snapshot, stages: {}, untitled: [] };
}

type Payload = Record<string, unknown>;

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function cardFromPayload(payload: Payload, previous?: MonitorCard): MonitorCard {
  return {
    attempt_id: str(payload.attempt_id) ?? previous?.attempt_id ?? "",
    card_number: str(payload.card_number) ?? previous?.card_number ?? "",
    incident_title: previous?.incident_title ?? "",
    state: str(payload.state) ?? previous?.state ?? "issued",
    response_status: str(payload.response_status) ?? previous?.response_status ?? "added",
    // Events carry codes only; the title is looked up when the tile renders.
    response_status_title: str(payload.response_status) ? "" : (previous?.response_status_title ?? ""),
    card_status: str(payload.card_status) ?? previous?.card_status ?? "registered",
    issued_at: str(payload.issued_at) ?? previous?.issued_at ?? new Date().toISOString(),
    received_at: str(payload.received_at) ?? previous?.received_at ?? null,
    primary_status_at: str(payload.primary_status_at) ?? previous?.primary_status_at ?? null,
    submitted_at: str(payload.submitted_at) ?? previous?.submitted_at ?? null,
    total: previous?.total ?? null,
    passed: previous?.passed ?? null,
  };
}

function updateStudent(
  state: MonitorState,
  studentId: string,
  update: (student: MonitorStudent) => MonitorStudent,
): MonitorState {
  const students = state.snapshot.students.map((s) => (s.student_id === studentId ? update(s) : s));
  return { ...state, snapshot: { ...state.snapshot, students } };
}

function studentOfAttempt(state: MonitorState, attemptId: string): MonitorStudent | undefined {
  return state.snapshot.students.find((s) => s.active.some((c) => c.attempt_id === attemptId));
}

/**
 * Applies one session event to the monitoring snapshot. Pure: the WebSocket handler calls
 * it for every event in order, so a reconnect that replays events converges to the same
 * state as a fresh snapshot.
 */
export function applyEvent(state: MonitorState, event: SessionEvent): MonitorState {
  const payload = event.payload as Payload;
  const attemptId = str(payload.attempt_id);
  switch (event.type) {
    case "session.started":
      return { ...state, snapshot: { ...state.snapshot, status: "running" } };
    case "session.finished":
      return {
        ...state,
        stages: {},
        snapshot: {
          ...state.snapshot,
          status: "finished",
          students: state.snapshot.students.map((s) => ({ ...s, active: [] })),
        },
      };
    case "attempt.issued": {
      if (!event.student_id || !attemptId) return state;
      const next = updateStudent(state, event.student_id, (s) =>
        s.active.some((c) => c.attempt_id === attemptId)
          ? s
          : { ...s, active: [...s.active, cardFromPayload(payload)] },
      );
      return { ...next, untitled: [...next.untitled, attemptId] };
    }
    case "attempt.received":
    case "attempt.status_changed":
    case "attempt.submitted": {
      if (!attemptId) return state;
      const student = studentOfAttempt(state, attemptId);
      if (!student) return state;
      return updateStudent(state, student.student_id, (s) => ({
        ...s,
        active: s.active.map((c) => (c.attempt_id === attemptId ? cardFromPayload(payload, c) : c)),
      }));
    }
    case "card.status_changed": {
      if (!attemptId) return state;
      const student = studentOfAttempt(state, attemptId);
      if (!student) return state;
      const cardStatus = str(payload.card_status) ?? "registered";
      return updateStudent(state, student.student_id, (s) => ({
        ...s,
        active: s.active.map((c) => (c.attempt_id === attemptId ? { ...c, card_status: cardStatus } : c)),
      }));
    }
    case "attempt.evaluated": {
      if (!attemptId) return state;
      const student = studentOfAttempt(state, attemptId);
      if (!student) return state;
      const total = typeof payload.total === "number" ? payload.total : null;
      const passed = payload.passed === true;
      const next = updateStudent(state, student.student_id, (s) => {
        const finished = s.finished + 1;
        const average =
          total === null ? s.average : Math.round((((s.average ?? 0) * s.finished + total) / finished) * 10) / 10;
        return {
          ...s,
          active: s.active.filter((c) => c.attempt_id !== attemptId),
          finished,
          passed: s.passed + (passed ? 1 : 0),
          average,
          last_total: total,
          last_attempt_id: attemptId,
        };
      });
      const stages = { ...next.stages };
      delete stages[student.student_id];
      return { ...next, stages };
    }
    case "attempt.progress": {
      if (!event.student_id) return state;
      const stage = str(payload.stage);
      if (!stage) return state;
      return { ...state, stages: { ...state.stages, [event.student_id]: { stage, at: event.at, attempt_id: attemptId } } };
    }
    case "dialog.turn": {
      // Call intake (wave 5+): every exchange with the caller keeps the tile «talking».
      if (!event.student_id) return state;
      return { ...state, stages: { ...state.stages, [event.student_id]: { stage: "talking", at: event.at, attempt_id: attemptId } } };
    }
    case "call.ringing":
    case "call.answered":
    case "call.ended": {
      // Call state of the telephony wave (PRD 12): the tile says what the call is doing.
      if (!event.student_id) return state;
      const stage = event.type === "call.ringing" ? "ringing" : event.type === "call.answered" ? "talking" : "call_ended";
      return { ...state, stages: { ...state.stages, [event.student_id]: { stage, at: event.at, attempt_id: attemptId } } };
    }
    default:
      return state;
  }
}

export type TileStatus = "idle" | "waiting" | "working" | "done";

/** Call intake: «waiting» is a ringing call (not answered yet), the norm runs from the
 * moment the call was taken (`received_at`) until the card is saved. */
function summarizeCall(
  student: MonitorStudent,
  normSeconds: number,
  passThreshold: number,
  now: number,
  cardsTotal: number,
  stage?: Stage,
): TileSummary {
  const ringing = student.active.filter((c) => c.state === "issued");
  const current =
    student.active.find((c) => c.attempt_id === stage?.attempt_id) ??
    [...student.active].sort((a, b) => b.issued_at.localeCompare(a.issued_at))[0] ??
    null;
  const overdue = student.active.some((c) => c.received_at && now - new Date(c.received_at).getTime() > normSeconds * 1000);
  const lowScore = student.last_total !== null && student.last_total < passThreshold;
  let status: TileStatus = "idle";
  if (student.active.length > 0) status = ringing.length === student.active.length ? "waiting" : "working";
  else if (cardsTotal > 0 && student.finished >= cardsTotal) status = "done";
  return { status, current, overdue, lowScore };
}

export interface TileSummary {
  status: TileStatus;
  /** The card whose timer is shown: the one the trainee is in, else the earliest without a
   * primary status, else the newest. */
  current: MonitorCard | null;
  overdue: boolean;
  lowScore: boolean;
}

/** What the tile shows for a trainee; `overdue` and `lowScore` drive the highlighting. */
export function summarize(
  student: MonitorStudent,
  normSeconds: number,
  passThreshold: number,
  now: number,
  cardsTotal: number,
  stage?: Stage,
  mode: string = "card_response",
): TileSummary {
  if (mode === "call_intake") return summarizeCall(student, normSeconds, passThreshold, now, cardsTotal, stage);
  const pending = student.active.filter((c) => !c.primary_status_at);
  const current =
    student.active.find((c) => c.attempt_id === stage?.attempt_id) ??
    pending.sort((a, b) => a.issued_at.localeCompare(b.issued_at))[0] ??
    [...student.active].sort((a, b) => b.issued_at.localeCompare(a.issued_at))[0] ??
    null;
  const overdue = pending.some((c) => now - new Date(c.issued_at).getTime() > normSeconds * 1000);
  const lowScore = student.last_total !== null && student.last_total < passThreshold;
  let status: TileStatus = "idle";
  if (student.active.length > 0) status = pending.length === student.active.length ? "waiting" : "working";
  else if (cardsTotal > 0 && student.finished >= cardsTotal) status = "done";
  return { status, current, overdue, lowScore };
}
