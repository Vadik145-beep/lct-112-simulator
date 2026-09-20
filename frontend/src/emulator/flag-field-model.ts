import type { AttemptOut, ServiceStatusOut } from "@/api/training";

/**
 * «Отметить ошибку» (issue #35): the dispatcher receives the card ready from the 112 operator
 * and checks it. A field can be flagged with the value the dispatcher considers right; the mark
 * can be removed until the card is closed. Nothing hints which field is wrong.
 */

export type FlaggedField = AttemptOut["flagged_fields"][number];

/** Field paths the evaluation engine knows (`app.domain.evaluation.data_check.FIELD_TITLES`). */
export const FIELD_TITLES: Record<string, string> = {
  "address.street": "Улица",
  "address.house": "Дом",
  "address.building": "Корпус",
  "address.structure": "Строение",
  "address.entrance": "Подъезд",
  "address.floor": "Этаж",
  "address.apartment": "Квартира",
  "address.descriptive": "Описательный адрес",
  incident_type: "Тип происшествия",
  "flags.injured": "Пострадавшие",
  services: "Оповещённые службы",
  "caller.name": "Заявитель",
  "caller.phone": "Телефон заявителя",
  description: "Описание",
};

export const ADDRESS_PARTS = ["street", "house", "building", "structure", "entrance", "floor", "apartment"] as const;

export interface FlagRequest {
  field: string;
  corrected_value: string;
}

/** «Услуга лишняя / не хватает» is encoded as «-code» / «+code» for the engine. */
export function serviceCorrection(kind: "extra" | "missing", code: string): string {
  return `${kind === "extra" ? "-" : "+"}${code.trim()}`;
}

export function describeCorrection(field: string, value: string, services: ServiceStatusOut[]): string {
  if (field === "services") {
    const sign = value.slice(0, 1);
    const code = value.slice(1);
    const title = services.find((s) => s.code === code)?.short_title ?? code;
    return sign === "-" ? `лишняя служба: ${title}` : `не хватает службы: ${title}`;
  }
  if (field === "flags.injured") return value === "true" ? "есть пострадавшие" : "пострадавших нет";
  return `верно: ${value}`;
}
