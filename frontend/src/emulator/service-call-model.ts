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
  not_taken: "доклад не принят",
  failed: "сбой связи",
};

/** The squad leader's report to the dispatcher (customer, 21.09.2026), not a call made by
 * the dispatcher: the panel names the other side differently and asks for no facts. */
export function isReport(call: ServiceCallOut): boolean {
  return call.kind === "report";
}

/** «Старший группы» for a report, «дежурный» for the officer of a service. */
export function otherSide(call: ServiceCallOut): string {
  return isReport(call) ? "старший группы" : "дежурный";
}

/** «Доклад бригады: ОДС ЖКХ» / «Звонок в службу: ОДС ЖКХ». */
export function callTitle(call: ServiceCallOut): string {
  return `${isReport(call) ? "Доклад бригады" : "Звонок в службу"}: ${call.service_title}`;
}

export function factTitle(code: string): string {
  return FACT_TITLES[code] ?? code;
}

/** «Звонок 00:47, переданы: адрес, тип» for the history of a service; a report reads
 * «Доклад бригады 00:20: «Прибытие»», and one nobody answered says so (issue #103). */
export function describeCall(call: ServiceCallOut): string {
  const length = call.seconds != null ? formatSeconds(call.seconds) : "идёт";
  if (isReport(call)) {
    const status = call.report_status_title ?? call.report_status ?? "";
    const about = status ? `: «${status}»` : "";
    if (call.ended_at && !call.answered) return `Доклад бригады не принят${about}`;
    return `Доклад бригады ${length}${about}`;
  }
  const passed = call.facts_passed.map(factTitle).join(", ") || "ничего";
  return `Звонок ${length}, переданы: ${passed}`;
}
