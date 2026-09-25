import { Circle, Loader2, Mic, Phone, PhoneOff, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { getAccessToken } from "@/api/token";
import { newActionId } from "@/emulator/draft";
import {
  END_REASON_TITLES,
  callTitle,
  factTitle,
  isCaller,
  isReport,
  otherSide,
  type ServiceCallOut,
} from "@/emulator/service-call-model";
import { formatSeconds } from "@/emulator/time";
import { cn } from "@/lib/utils";
import { watchLevel } from "@/softphone/sip-phone";

/**
 * A call of the dispatcher to a service officer (issue #36, «звено Б → В») or an incoming
 * report of the squad (issue #103): the transcript, the facts passed so far, a text field
 * (the fallback of the voice path) and «Завершить». With telephony the voice goes through the
 * softphone (it answers a call the dispatcher started by itself); without it the officer's
 * replies play here and the dispatcher may speak into the browser microphone (issue #60).
 * A report is not answered for the trainee: it rings until «Ответить».
 */

// A shorter recording is a click without holding the button, not a phrase.
const MIN_RECORDING_SECONDS = 0.6;

/** The microphone chosen in the softphone settings; the system default when it is gone. */
async function openMicrophone(deviceId: string | null): Promise<MediaStream> {
  if (deviceId) {
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: { deviceId: { exact: deviceId } },
      });
    } catch {
      /* the chosen device is unplugged: fall back to the default one */
    }
  }
  return navigator.mediaDevices.getUserMedia({ audio: true });
}

export function ServiceCallPanel({
  call,
  telephony,
  sttAvailable,
  micDeviceId,
  devices,
  onMicDevice,
  onMicOpened,
  pending,
  replying = false,
  awaitingReport = false,
  error,
  onSay,
  onSpeak,
  onAnswer,
  cloud,
  onEnd,
}: {
  call: ServiceCallOut;
  /** The voice goes through the softphone: no replies played here. */
  telephony: boolean;
  /** The stt service answers: the browser microphone can be used instead of typing. */
  sttAvailable: boolean;
  /** The microphone chosen in the softphone settings (null = the system default). */
  micDeviceId: string | null;
  /** Microphones the browser knows; the list fills in after the first permission. */
  devices: MediaDeviceInfo[];
  onMicDevice: (deviceId: string | null) => void;
  onMicOpened: () => void;
  pending: boolean;
  /** The dispatcher's phrase is on its way and the other side's answer is awaited. */
  replying?: boolean;
  /** The call to the own service is over and the squad leader will call with a report. */
  awaitingReport?: boolean;
  error: string | null;
  onSay: (text: string, actionId: string) => void;
  onSpeak: (blob: Blob, actionId: string) => void;
  /** «Ответить» on an incoming report of the squad (issue #103). */
  onAnswer: () => void;
  /**
   * Облачный разговор (issue #59): «connecting» — соединяемся с Vapi, «live» — говорим
   * голосом, «failed» — облако не поднялось и разговор идёт текстом и микрофоном.
   */
  cloud: "off" | "connecting" | "live" | "failed";
  onEnd: () => void;
}) {
  const [text, setText] = useState("");
  const [recording, setRecording] = useState(false);
  const [recordedFor, setRecordedFor] = useState(0);
  const [micNote, setMicNote] = useState<string | null>(null);
  const [micLevel, setMicLevel] = useState(0);
  const stopLevel = useRef<(() => void) | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  // The last reply played, per call: the panel stays mounted from one call of the card to the
  // next, and every call numbers its turns from zero again.
  const played = useRef(new Map<string, number>());
  const log = useRef<HTMLOListElement>(null);
  const actionId = useRef<string | null>(null);
  const open = call.ended_at === null;
  const canSpeak =
    sttAvailable && !telephony && typeof MediaRecorder !== "undefined";

  // A call that ends while the microphone is on: stop the recorder, drop the recording.
  useEffect(() => {
    if (!open && recorder.current) {
      chunks.current = [];
      recorder.current.stop();
    }
  }, [open]);

  // Seconds of the current recording, shown next to the button.
  useEffect(() => {
    if (!recording) return;
    const startedAt = Date.now();
    setRecordedFor(0);
    const timer = setInterval(
      () => setRecordedFor((Date.now() - startedAt) / 1000),
      250,
    );
    return () => clearInterval(timer);
  }, [recording]);

  // The officer's newest reply plays once (browser mode only).
  useEffect(() => {
    const last = call.turns[call.turns.length - 1];
    if (!last || last.role !== "caller") return;
    if (last.index <= (played.current.get(call.id) ?? -1)) return;
    played.current.set(call.id, last.index);
    if (!last.audio_url || telephony) return;
    void play(last.audio_url);
  }, [call.id, call.turns, telephony]);

  // Only the transcript scrolls: scrollIntoView moved the whole page as well and pushed the
  // services strip off the screen.
  useEffect(() => {
    const transcript = log.current;
    if (transcript) transcript.scrollTop = transcript.scrollHeight;
  }, [call.turns.length, replying]);

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

  // Push to talk exactly as in the 112 operator's card: hold the button and speak, release
  // to send. A click without holding records nothing.
  async function startRecording() {
    if (recorder.current || pending || !open) return;
    setMicNote(null);
    try {
      const stream = await openMicrophone(micDeviceId);
      onMicOpened();
      stopLevel.current = watchLevel(stream, setMicLevel);
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "";
      const rec = new MediaRecorder(
        stream,
        mime ? { mimeType: mime } : undefined,
      );
      const startedAt = Date.now();
      chunks.current = [];
      rec.ondataavailable = (e) => chunks.current.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        stopLevel.current?.();
        stopLevel.current = null;
        setMicLevel(0);
        recorder.current = null;
        setRecording(false);
        const blob = new Blob(chunks.current, {
          type: rec.mimeType || "audio/webm",
        });
        chunks.current = [];
        const seconds = (Date.now() - startedAt) / 1000;
        if (seconds < MIN_RECORDING_SECONDS) {
          setMicNote(
            "Слишком короткая запись: удерживайте кнопку, пока говорите, и отпустите.",
          );
          return;
        }
        if (blob.size > 0 && open) {
          actionId.current = actionId.current ?? newActionId();
          onSpeak(blob, actionId.current);
        }
      };
      recorder.current = rec;
      rec.start();
      setRecording(true);
    } catch {
      setRecording(false);
      setMicNote(
        "Микрофон недоступен: разрешите доступ в браузере или выберите другое устройство.",
      );
    }
  }

  function stopRecording() {
    const rec = recorder.current;
    if (rec && rec.state !== "inactive") rec.stop();
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
        // The panel takes the room left in the trainer panel; of that room only the
        // transcript grows (from three lines up), the buttons and the input keep their
        // size, and a longer conversation scrolls inside the transcript.
        "flex grow flex-col gap-2 rounded-sm border p-2 text-xs *:shrink-0",
        open
          ? "border-[var(--arm-blue)] bg-[#eef3fb]"
          : "border-[#a9adb2] bg-[var(--arm-field)]",
      )}
      aria-label={callTitle(call)}
      data-testid="service-call-panel"
      data-kind={call.kind}
      data-state={open ? (call.answered ? "talking" : "ringing") : "ended"}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1 font-semibold">
          <Phone className="size-3.5" aria-hidden />
          {isReport(call)
            ? `Доклад бригады: ${call.service_title}`
            : isCaller(call)
              ? `Заявитель: ${call.service_title}`
              : call.service_title}
        </span>
        <span className="text-[var(--arm-text-muted)]">
          {!open
            ? `завершён${call.seconds != null ? `, ${formatSeconds(call.seconds)}` : ""}`
            : call.answered
              ? telephony
                ? "разговор по телефону"
                : isReport(call)
                  ? "входящий доклад"
                  : "разговор"
              : isReport(call)
                ? "входящий вызов…"
                : "вызов…"}
        </span>
      </div>
      <ol
        ref={log}
        className="flex min-h-16 grow basis-16 flex-col gap-1 overflow-y-auto"
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
              {t.role === "caller" ? otherSide(call) : "вы"}
              {t.heard ? " (распознано)" : ""}:{" "}
            </span>
            {t.text}
          </li>
        ))}
        {/* Our model is answering; a cloud call speaks for itself (Vapi). */}
        {open && replying && cloud === "off" && (
          <li
            className="flex items-center gap-1.5 rounded-sm bg-white px-2 py-1 text-[var(--arm-text-muted)]"
            role="status"
            data-testid="service-call-replying"
          >
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
            {otherSide(call)} отвечает…
          </li>
        )}
        {call.turns.length === 0 && (
          <li className="text-[var(--arm-text-muted)]">
            {isReport(call)
              ? "Звонит старший группы: ответьте, чтобы выслушать доклад."
              : isCaller(call)
                ? "Звоним заявителю…"
                : "Соединение со службой…"}
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
      {open && cloud !== "off" && (
        <div className="text-[10px] text-[var(--arm-text-muted)]" data-testid="service-cloud">
          {cloud === "connecting" && "Соединяем с облачным голосом…"}
          {cloud === "live" && "Разговор голосом через облако: говорите в гарнитуру."}
          {cloud === "failed" &&
            "Облако недоступно: отвечает локальная модель, пишите или говорите в микрофон."}
        </div>
      )}
      {open && call.answered && cloud !== "live" && (
        <form
          className="flex gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <input
            aria-label={`Сказать: ${otherSide(call)}`}
            placeholder={
              telephony
                ? `или напишите: ${otherSide(call)}…`
                : `скажите: ${otherSide(call)}…`
            }
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={pending}
            className="h-7 flex-1 border-b border-[#a9adb2] bg-white px-2 text-xs focus:border-[var(--arm-blue)] focus:outline-none"
          />
          <button
            type="submit"
            aria-label={`Отправить: ${otherSide(call)}`}
            disabled={pending || !text.trim()}
            className="flex size-7 items-center justify-center rounded-sm bg-[var(--arm-blue)] text-white disabled:opacity-50"
          >
            <Send className="size-3.5" aria-hidden />
          </button>
        </form>
      )}
      {open && call.answered && cloud !== "live" && canSpeak && devices.length > 0 && (
        <label className="flex items-center gap-1 text-[10px] text-[var(--arm-text-muted)]">
          <Mic className="size-3" aria-hidden />
          <select
            aria-label="Микрофон"
            value={micDeviceId ?? ""}
            onChange={(e) => onMicDevice(e.target.value || null)}
            className="max-w-[11rem] border-b border-[#a9adb2] bg-white px-1 text-[10px] focus:outline-none"
          >
            <option value="">Микрофон по умолчанию</option>
            {devices.map((d, i) => (
              <option key={d.deviceId || i} value={d.deviceId}>
                {d.label || `Микрофон ${i + 1}`}
              </option>
            ))}
          </select>
        </label>
      )}
      {open && call.answered && cloud !== "live" && canSpeak && (
        <button
          type="button"
          aria-label={
            recording
              ? "Говорите, отпустите, чтобы отправить"
              : "Удерживайте и говорите"
          }
          aria-pressed={recording}
          title={
            recording
              ? "Отпустите, чтобы отправить сказанное"
              : "Удерживайте кнопку и говорите дежурному"
          }
          disabled={pending}
          onPointerDown={(e) => {
            e.preventDefault();
            // The button holds the pointer while pressed: when the panel shifts (the
            // recording line appears, the microphone list fills in) the button may move away
            // from the cursor, and the recording must go on until the button is released.
            e.currentTarget.setPointerCapture(e.pointerId);
            void startRecording();
          }}
          onPointerUp={stopRecording}
          onPointerLeave={stopRecording}
          onPointerCancel={stopRecording}
          onContextMenu={(e) => e.preventDefault()}
          data-testid="service-call-talk"
          className={cn(
            "inline-flex h-7 w-full select-none items-center justify-center gap-1 rounded-sm border px-2 disabled:opacity-50",
            recording
              ? "border-[var(--arm-orange)] bg-[var(--arm-orange)] text-white"
              : "border-[#a9adb2] bg-white text-[var(--arm-text)] hover:bg-[var(--arm-panel-2)]",
          )}
        >
          {recording ? (
            <Circle className="size-3.5 fill-current" aria-hidden />
          ) : (
            <Mic className="size-3.5" aria-hidden />
          )}
          {recording ? "Говорите… отпустите, чтобы отправить" : "Удерживайте и говорите"}
        </button>
      )}
      {recording && (
        <span
          className="flex items-center gap-2 text-[10px] text-[var(--arm-orange)]"
          role="status"
        >
          <span>
            Говорите, {recordedFor.toFixed(0)} с. Отпустите, чтобы отправить.
          </span>
          <span
            className="h-1.5 w-16 overflow-hidden rounded bg-[#cfd2d4]"
            aria-label="Уровень микрофона"
          >
            <span
              className={cn(
                "block h-full",
                micLevel > 0.02
                  ? "bg-[var(--arm-green)]"
                  : "bg-[var(--arm-red)]",
              )}
              style={{ width: `${Math.min(100, Math.round(micLevel * 300))}%` }}
            />
          </span>
        </span>
      )}
      {micNote && !recording && (
        <span className="text-[10px] text-[var(--arm-red)]" role="alert">
          {micNote}
        </span>
      )}
      {open && call.answered && !telephony && !sttAvailable && (
        <span className="text-[10px] text-[var(--arm-text-muted)]">
          Распознавание речи недоступно: пишите текстом ({otherSide(call)}).
        </span>
      )}
      {open && !call.answered && isReport(call) && (
        <button
          type="button"
          onClick={onAnswer}
          disabled={pending}
          data-testid="answer-report"
          className="inline-flex h-7 items-center justify-center gap-1 rounded-sm bg-[var(--arm-green)] px-2 font-semibold text-white hover:opacity-90 disabled:opacity-50"
        >
          <Phone className="size-3.5" aria-hidden /> Ответить
        </button>
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
      {!open && awaitingReport && call.end_reason === "hangup" && (
        <span className="font-semibold text-[var(--arm-blue-dark)]" data-testid="await-report">
          Ожидайте доклад: старший группы позвонит сам.
        </span>
      )}
      {error && !recording && (
        <span role="alert" className="text-[var(--arm-red)]">
          {error}
        </span>
      )}
    </section>
  );
}
