export const MODE_TITLES: Record<string, string> = {
  card_response: "Реагирование на карточку (ДДС)",
  call_intake: "Приём вызова (оператор 112)",
};

export const SESSION_STATUS_TITLES: Record<string, string> = {
  draft: "Не начато",
  running: "Идёт",
  finished: "Завершено",
};

export const DIFFICULTY_TITLES: Record<number, string> = {
  1: "1 — простой случай, без ошибок оператора 112",
  2: "2 — одна ошибка оператора 112 в карточке",
  3: "3 — ЧС, две ошибки, вал: 3 карточки по одной каждые 40–50 с",
};

/** With cards written by AI there is no stream: one card at a time at every difficulty. */
export const AI_DIFFICULTY_TITLES: Record<number, string> = {
  ...DIFFICULTY_TITLES,
  3: "3 — ЧС, две ошибки оператора 112",
};

export function difficultyTitle(difficulty: number, ai: boolean): string {
  return (ai ? AI_DIFFICULTY_TITLES : DIFFICULTY_TITLES)[difficulty] ?? String(difficulty);
}

/** How many cards are on the trainee's screen at once at each difficulty (backend CARDS_AT_ONCE). */
export const CARDS_AT_ONCE: Record<number, number> = { 1: 1, 2: 1, 3: 3 };

/** «Вал карточек» at difficulty 3 of a card response lesson (backend app.training.stream): the
 * first card at once, then one every 40–50 s, three in all (a repeated call among them). */
export const STREAM = { difficulty: 3, cards: 3, gap: "40–50" } as const;

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
