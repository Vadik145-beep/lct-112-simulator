import type { BadgeTone } from "@/components/ui/badge";

export const SCENARIO_STATUS_TITLES: Record<string, string> = {
  draft: "Черновик",
  review: "На проверке",
  approved: "Утверждён",
  archived: "В архиве",
};

export const SCENARIO_STATUS_TONES: Record<string, BadgeTone> = {
  draft: "neutral",
  review: "warning",
  approved: "success",
  archived: "neutral",
};

export const SOURCE_TITLES: Record<string, string> = {
  ticket: "Билет, проверен",
  organizers: "Билет, черновик",
  generated: "По фразе",
  manual: "Вручную",
  student: "Обучающийся",
};

export const VOICING_TITLES: Record<string, string> = {
  none: "без озвучки",
  queued: "озвучивается…",
  done: "озвучена",
  failed: "озвучка не удалась",
};

export const DIFFICULTY_SHORT: Record<number, string> = { 1: "лёгкий", 2: "средний", 3: "сложный" };

export const INCIDENT_FLAG_TITLES: Record<string, string> = {
  injured: "Пострадавшие",
  no_access: "Нет доступа",
  threat: "Угроза людям",
  offense: "Правонарушение",
  not_on_site: "Пострадавшие не на месте",
  gasification: "Газификация",
  medical_help: "Нужна медпомощь",
  evacuation: "Нужна эвакуация",
  mass_incident: "Более 5 человек",
  road_closed: "Перекрытие движения",
  tunnel: "Тоннель",
  pedestrian_bridge: "Пешеходный мост",
  road_bridge: "Автомобильный мост",
  telecom_object: "Объект связи",
  construction_site: "Стройка",
  listed_object: "Объект из перечня",
};

export const ADDRESS_FIELD_TITLES: Record<string, string> = {
  region: "Регион",
  city: "Город",
  street: "Улица",
  house: "Дом",
  building: "Корпус",
  structure: "Строение",
  entrance: "Подъезд",
  floor: "Этаж",
  code: "Код домофона",
  apartment: "Квартира",
  okrug: "Округ",
  district: "Район",
  descriptive: "Со слов заявителя",
};

export const FACT_TITLES: Record<string, string> = {
  what_happened: "Что случилось",
  address: "Адрес",
  region: "Регион",
  injured: "Пострадавшие",
  danger: "Угроза",
  caller_name: "Заявитель",
  callback_phone: "Телефон",
  floor: "Этаж",
  storeys: "Этажность",
  gas: "Газ",
  vehicle: "Транспорт",
  age: "Возраст",
};

export const DECISION_TITLES: Record<string, string> = { accept: "Принять", reject: "Не принимать" };

export function formatAddress(address: Record<string, unknown> | undefined | null): string {
  if (!address) return "—";
  const order = ["region", "city", "street", "house", "building", "structure", "entrance", "floor", "apartment", "code"];
  const parts = order
    .filter((k) => address[k])
    .map((k) => (k === "street" || k === "region" || k === "city" ? String(address[k]) : `${(ADDRESS_FIELD_TITLES[k] ?? k).toLowerCase()} ${String(address[k])}`));
  const exact = parts.join(", ");
  const descriptive = address.descriptive ? String(address.descriptive) : "";
  if (exact && descriptive) return `${exact} (${descriptive})`;
  return exact || descriptive || "—";
}
