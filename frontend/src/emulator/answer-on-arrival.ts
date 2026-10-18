import type { NavigateFunction } from "react-router-dom";

import type { RingingOut } from "@/api/training";

/** The navigation state of «Перейти в карточку и ответить»: the call to pick up there. */
export type AnswerOnArrival = { answerCallId?: string };

/**
 * Leads to the card of a ringing call and asks its phone to pick up at once (стенд
 * 18.10.2026, Б36: in the card the call still waited for a second press).
 */
export function goAndAnswer(navigate: NavigateFunction, call: RingingOut) {
  const state: AnswerOnArrival = { answerCallId: call.call_id };
  navigate(`/student/attempts/${call.attempt_id}`, { state });
}
