export const MODE_TITLES: Record<string, string> = {
  card_response: "Реагирование на карточку",
  call_intake: "Приём вызова",
};

export const SESSION_STATUS_TITLES: Record<string, string> = {
  draft: "Не начато",
  running: "Идёт",
  finished: "Завершено",
};

export const DIFFICULTY_TITLES: Record<number, string> = {
  1: "1 — одна карточка, спокойный темп",
  2: "2 — одна карточка, сложные случаи",
  3: "3 — несколько карточек одновременно",
};

/** A score as the report shows it: one decimal, «—» when there is nothing yet. */
export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/** Seconds as «12 с» or «1 мин 05 с». */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.round(seconds);
  if (total < 60) return `${total} с`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m} мин ${String(s).padStart(2, "0")} с`;
}

/** Signed deviation from the norm: «+12 с» is late, «−5 с» is early. */
export function formatDeviation(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const rounded = Math.round(seconds);
  if (rounded === 0) return "в норме";
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded)} с`;
}

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

/** Response statuses of the service (memo, page 24) for tiles built from events. */
export const RESPONSE_STATUS_TITLES: Record<string, string> = {
  added: "Добавлена",
  received: "Получена службой",
  accepted: "Принята",
  rejected: "Не принята",
  response_started: "Начало реагирования",
  arrived: "Прибытие",
  works_started: "Проведение работ",
  works_done: "Работы завершены",
  works_refused: "Отказ от выполнения работ",
};
