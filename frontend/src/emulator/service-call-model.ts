import type { AttemptOut } from "@/api/training";
import { formatSeconds } from "@/emulator/time";

/** Helpers of a call to a service officer (issue #36) shared by the card and the review. */

export type ServiceCallOut = AttemptOut["service_calls"][number];

export const FACT_TITLES: Record<string, string> = {
  address: "адрес",
  incident_type: "тип происшествия",
  injured: "пострадавшие",
  order_number: "номер наряда",
  access: "доступ",
};

export const END_REASON_TITLES: Record<string, string> = {
  hangup: "вы завершили",
  card_closed: "закрыта карточка",
  no_answer: "дежурный не ответил",
  failed: "сбой связи",
};

export function factTitle(code: string): string {
  return FACT_TITLES[code] ?? code;
}

/** «Звонок 00:47, переданы: адрес, тип» for the history of a service. */
export function describeCall(call: ServiceCallOut): string {
  const length = call.seconds != null ? formatSeconds(call.seconds) : "идёт";
  const passed = call.facts_passed.map(factTitle).join(", ") || "ничего";
  return `Звонок ${length}, переданы: ${passed}`;
}
