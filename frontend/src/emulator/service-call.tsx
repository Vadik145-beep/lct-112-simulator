import { Phone, PhoneOff, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { getAccessToken } from "@/api/token";
import { newActionId } from "@/emulator/draft";
import {
  END_REASON_TITLES,
  factTitle,
  type ServiceCallOut,
} from "@/emulator/service-call-model";
import { formatSeconds } from "@/emulator/time";
import { cn } from "@/lib/utils";

/**
 * A call of the dispatcher to a service officer (issue #36, «звено Б → В»): the transcript,
 * the facts passed so far, a text field (the fallback of the voice path) and «Завершить».
 * With telephony the voice goes through the softphone (it answers the officer's call by
 * itself); without it the officer's replies play here.
 */

export function ServiceCallPanel({
  call,
  telephony,
  pending,
  error,
  onSay,
  onEnd,
}: {
  call: ServiceCallOut;
  /** The voice goes through the softphone: no replies played here. */
  telephony: boolean;
  pending: boolean;
  error: string | null;
  onSay: (text: string, actionId: string) => void;
  onEnd: () => void;
}) {
  const [text, setText] = useState("");
  const audio = useRef<HTMLAudioElement | null>(null);
  const played = useRef(-1);
  const log = useRef<HTMLOListElement>(null);
  const actionId = useRef<string | null>(null);
  const open = call.ended_at === null;

  // The officer's newest reply plays once (browser mode only).
  useEffect(() => {
    const last = call.turns[call.turns.length - 1];
    if (!last || last.role !== "caller" || last.index <= played.current) return;
    played.current = last.index;
    if (!last.audio_url || telephony) return;
    void play(last.audio_url);
  }, [call.turns, telephony]);

  useEffect(() => {
    log.current?.lastElementChild?.scrollIntoView({ block: "nearest" });
  }, [call.turns.length]);

  useEffect(() => {
    if (!pending) actionId.current = null;
  }, [pending, call.turns.length]);

  function submit() {
    const phrase = text.trim();
    if (!phrase || pending || !open) return;
    actionId.current = actionId.current ?? newActionId();
    onSay(phrase, actionId.current);
    setText("");
  }

  async function play(url: string) {
    if (!audio.current) audio.current = new Audio();
    const el = audio.current;
    try {
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
      });
      if (!res.ok) return;
      if (el.src.startsWith("blob:")) URL.revokeObjectURL(el.src);
      el.src = URL.createObjectURL(await res.blob());
      await el.play();
    } catch {
      /* no voice: the text is on the screen */
    }
  }

  const missing = call.facts_required.filter(
    (f) => !call.facts_passed.includes(f),
  );
  return (
    <section
      className={cn(
        "flex flex-col gap-2 rounded-sm border p-2 text-xs",
        open
          ? "border-[var(--arm-blue)] bg-[#eef3fb]"
          : "border-[#a9adb2] bg-[var(--arm-field)]",
      )}
      aria-label={`Звонок в службу: ${call.service_title}`}
      data-testid="service-call-panel"
      data-state={open ? (call.answered ? "talking" : "ringing") : "ended"}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1 font-semibold">
          <Phone className="size-3.5" aria-hidden />
          {call.service_title}
        </span>
        <span className="text-[var(--arm-text-muted)]">
          {!open
            ? `завершён${call.seconds != null ? `, ${formatSeconds(call.seconds)}` : ""}`
            : call.answered
              ? telephony
                ? "разговор по телефону"
                : "разговор"
              : "вызов…"}
        </span>
      </div>
      <ol
        ref={log}
        className="flex max-h-48 flex-col gap-1 overflow-y-auto"
        aria-label="Стенограмма звонка"
      >
        {call.turns.map((t) => (
          <li
            key={t.index}
            data-role={t.role === "caller" ? "officer" : "dispatcher"}
            className={cn(
              "rounded-sm px-2 py-1",
              t.role === "caller"
                ? "bg-white"
                : "bg-[var(--arm-panel-2)] text-right",
            )}
          >
            <span className="text-[10px] text-[var(--arm-text-muted)]">
              {t.role === "caller" ? "дежурный" : "вы"}
              {t.heard ? " (распознано)" : ""}:{" "}
            </span>
            {t.text}
          </li>
        ))}
        {call.turns.length === 0 && (
          <li className="text-[var(--arm-text-muted)]">
            Соединение со службой…
          </li>
        )}
      </ol>
      {call.facts_required.length > 0 && (
        <div data-testid="service-call-facts">
          <span className="text-[var(--arm-text-muted)]">Передано: </span>
          {call.facts_passed.length > 0
            ? call.facts_passed.map(factTitle).join(", ")
            : "пока ничего"}
          {open && missing.length > 0 && (
            <>
              <span className="text-[var(--arm-text-muted)]">
                {" "}
                · осталось:{" "}
              </span>
              {missing.map(factTitle).join(", ")}
            </>
          )}
        </div>
      )}
      {open && call.answered && (
        <form
          className="flex gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <input
            aria-label="Сказать дежурному"
            placeholder={
              telephony ? "или напишите дежурному…" : "скажите дежурному…"
            }
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={pending}
            className="h-7 flex-1 border-b border-[#a9adb2] bg-white px-2 text-xs focus:border-[var(--arm-blue)] focus:outline-none"
          />
          <button
            type="submit"
            aria-label="Отправить дежурному"
            disabled={pending || !text.trim()}
            className="flex size-7 items-center justify-center rounded-sm bg-[var(--arm-blue)] text-white disabled:opacity-50"
          >
            <Send className="size-3.5" aria-hidden />
          </button>
        </form>
      )}
      {open && (
        <button
          type="button"
          onClick={onEnd}
          disabled={pending}
          className="inline-flex h-7 items-center justify-center gap-1 rounded-sm bg-[var(--arm-red)] px-2 font-semibold text-white hover:opacity-90 disabled:opacity-50"
        >
          <PhoneOff className="size-3.5" aria-hidden /> Завершить звонок
        </button>
      )}
      {!open && call.end_reason && (
        <span className="text-[var(--arm-text-muted)]">
          {END_REASON_TITLES[call.end_reason] ?? call.end_reason}
        </span>
      )}
      {error && (
        <span role="alert" className="text-[var(--arm-red)]">
          {error}
        </span>
      )}
    </section>
  );
}
