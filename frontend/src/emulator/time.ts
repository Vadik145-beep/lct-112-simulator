import { useEffect, useState } from "react";

const TIME_ZONE = "Europe/Moscow";

/** Current time, re-rendered every second (the ARM clock and the acceptance timers). */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function formatTime(iso: string | null | undefined, withSeconds = true): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("ru-RU", {
    timeZone: TIME_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    ...(withSeconds ? { second: "2-digit" } : {}),
  });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("ru-RU", {
    timeZone: TIME_ZONE,
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
  });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return `${formatDate(iso)} в ${formatTime(iso)}`;
}

export function formatLongDate(ts: number): string {
  const text = new Date(ts).toLocaleDateString("ru-RU", {
    timeZone: TIME_ZONE,
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
  return text.charAt(0).toUpperCase() + text.slice(1).replace(" г.", "");
}

export function clockParts(ts: number): { hm: string; s: string } {
  const text = new Date(ts).toLocaleTimeString("ru-RU", {
    timeZone: TIME_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  return { hm: text.slice(0, 5), s: text.slice(6, 8) };
}

/** mm:ss of a number of seconds (negative values are shown with a minus). */
export function formatSeconds(total: number): string {
  const sign = total < 0 ? "-" : "";
  const abs = Math.abs(Math.round(total));
  const m = Math.floor(abs / 60);
  const s = abs % 60;
  return `${sign}${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export type TimerPhase = "ok" | "warning" | "overdue" | "done" | "late";

export interface AcceptanceTimer {
  /** Seconds elapsed from «Добавлена» to now (or to the primary status). */
  elapsed: number;
  /** Seconds left until the norm; negative when overdue. */
  remaining: number;
  phase: TimerPhase;
  /** Elapsed / norm, capped at 1 for the colour scale. */
  progress: number;
}

// Colour thresholds of the acceptance timer (plan wave 3): normal until 80 %, warning until 100 %.
const WARNING_SHARE = 0.8;

/**
 * State of the 30-second acceptance timer of a card. Stops at the primary status; `done`
 * means it was set in time, `late` after the norm.
 */
export function acceptanceTimer(
  issuedAt: string,
  primaryStatusAt: string | null | undefined,
  normSeconds: number,
  now: number,
): AcceptanceTimer {
  const start = new Date(issuedAt).getTime();
  const end = primaryStatusAt ? new Date(primaryStatusAt).getTime() : now;
  const elapsed = Math.max(0, (end - start) / 1000);
  const remaining = normSeconds - elapsed;
  let phase: TimerPhase;
  if (primaryStatusAt) phase = elapsed <= normSeconds ? "done" : "late";
  else if (elapsed > normSeconds) phase = "overdue";
  else if (elapsed >= normSeconds * WARNING_SHARE) phase = "warning";
  else phase = "ok";
  return { elapsed, remaining, phase, progress: Math.min(1, elapsed / normSeconds) };
}
