import { Check, Circle, Ear, Mic, Send, Volume2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useAskTopic, useSay, useUtterance, type DialogOut, type DialogTurnOut } from "@/api/intake";
import { getAccessToken } from "@/api/token";
import { NetworkError } from "@/api/training";
import { formatTime } from "@/emulator/time";
import { ArmButton } from "@/emulator/widgets";
import { newId } from "@/intake/draft";
import { cn } from "@/lib/utils";

const DIALOG_MODE_TITLES: Record<string, string> = {
  select: "Готовые реплики",
  hybrid: "Готовые + новые",
  generate: "Свободная генерация",
  buttons: "Кнопки тем",
  live: "Живой режим",
  cloud: "Облачный голос",
};

/**
 * The conversation with the caller in the training panel (PRD 13.5): the transcript with
 * topics, «заявитель говорит» while a reply plays, «слушаю вас» while the microphone is on,
 * the text field when telephony is off, and «Что выяснить» with the required topics.
 */
export function DialogPanel({
  attemptId,
  dialog,
  active,
  hints,
  telephony,
  input = true,
  onCallerSpoke,
}: {
  attemptId: string;
  dialog: DialogOut;
  /** The call is answered and not over: the operator may speak. */
  active: boolean;
  hints: boolean;
  telephony: boolean;
  /** Own text field and microphone; off when the softphone panel provides them. */
  input?: boolean;
  /** The caller's reply that just arrived (to notice a hang-up, for example). */
  onCallerSpoke?: (turn: DialogTurnOut, callEnded: boolean) => void;
}) {
  const say = useSay(attemptId);
  const askTopic = useAskTopic(attemptId);
  const utterance = useUtterance(attemptId);
  const [text, setText] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const [recording, setRecording] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const audio = useRef<HTMLAudioElement | null>(null);
  const played = useRef<number>(-1);
  const actionId = useRef<string | null>(null);
  const log = useRef<HTMLOListElement>(null);
  const spoke = useRef(onCallerSpoke);
  spoke.current = onCallerSpoke;

  const busy = say.isPending || askTopic.isPending || utterance.isPending;
  const error = say.error ?? askTopic.error ?? utterance.error;

  // Voice of the caller: the newest reply with a file plays once, after the operator's
  // gesture (answering, sending) so autoplay is allowed.
  useEffect(() => {
    const last = dialog.turns[dialog.turns.length - 1];
    if (!last || last.role !== "caller" || last.index <= played.current) return;
    played.current = last.index;
    if (!last.audio_url || telephony) return;
    void play(last.audio_url);
  }, [dialog.turns, telephony]);

  useEffect(() => {
    log.current?.lastElementChild?.scrollIntoView({ block: "nearest" });
  }, [dialog.turns.length]);

  const settle = useCallback(
    (data: { caller: DialogTurnOut; call_ended?: boolean }) => {
      actionId.current = null;
      setText("");
      spoke.current?.(data.caller, Boolean(data.call_ended));
    },
    [],
  );

  const send = useCallback(() => {
    const phrase = text.trim();
    if (!phrase || busy || !active) return;
    actionId.current = actionId.current ?? newId();
    say.mutate({ text: phrase, action_id: actionId.current }, { onSuccess: settle });
  }, [active, busy, say, settle, text]);

  const ask = (topic: string) => {
    if (busy || !active) return;
    askTopic.mutate({ topic, action_id: newId() }, { onSuccess: settle });
  };

  const toggleMic = async () => {
    if (recording) {
      recorder.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunks.current = [];
      rec.ondataavailable = (e) => chunks.current.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        const blob = new Blob(chunks.current, { type: rec.mimeType || "audio/webm" });
        if (blob.size > 0) utterance.mutate({ blob, action_id: newId() }, { onSuccess: settle });
      };
      recorder.current = rec;
      rec.start();
      setRecording(true);
    } catch {
      setRecording(false);
    }
  };

  const required = dialog.topics.filter((t) => t.required);
  const covered = required.filter((t) => t.covered).length;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="dialog-panel">
      <audio ref={audio} onEnded={() => setSpeaking(false)} onPause={() => setSpeaking(false)} hidden />
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium">Разговор</span>
        {speaking ? (
          <span className="inline-flex items-center gap-1 text-[var(--arm-orange)]" role="status">
            <Volume2 className="size-3.5 animate-pulse" aria-hidden /> заявитель говорит
          </span>
        ) : recording ? (
          <span className="inline-flex items-center gap-1 text-[var(--arm-green)]" role="status">
            <Ear className="size-3.5 animate-pulse" aria-hidden /> слушаю вас
          </span>
        ) : busy ? (
          <span className="text-[var(--arm-text-muted)]" role="status">
            заявитель думает…
          </span>
        ) : null}
      </div>
      {dialog.fallback_replies > 0 && (
        <p className="rounded-sm border border-[var(--arm-red)] bg-white px-2 py-1 text-[11px] leading-snug text-[var(--arm-red)]" role="alert" data-testid="dialog-fallback-warning">
          {dialog.mode === "cloud"
            ? `Облачный голос был недоступен: ${dialog.fallback_replies === 1 ? "ответ дан" : `${dialog.fallback_replies} ответов даны`} локальной моделью из утверждённых реплик.`
            : `Модель заявителя недоступна: ${dialog.fallback_replies === 1 ? "ответ подобран" : `${dialog.fallback_replies} ответов подобраны`} по ключевым словам, а не режимом «${DIALOG_MODE_TITLES[dialog.requested_mode] ?? dialog.requested_mode}».`}
        </p>
      )}
      <ol ref={log} className="arm-scroll flex min-h-24 flex-1 flex-col gap-1.5 overflow-y-auto rounded-sm bg-white p-2 text-xs" aria-label="Стенограмма разговора">
        {dialog.turns.length === 0 && <li className="text-[var(--arm-text-muted)]">Ответьте на вызов: заявитель заговорит первым.</li>}
        {dialog.turns.map((t) => (
          <li key={t.index} className={cn("flex flex-col", t.role === "operator" ? "items-end" : "items-start")} data-role={t.role}>
            <span
              className={cn(
                "max-w-[90%] rounded-md px-2 py-1 leading-snug",
                t.role === "operator" ? "bg-[var(--arm-blue)] text-white" : "bg-[var(--arm-panel-2)]",
              )}
            >
              {t.text}
            </span>
            <span className="mt-0.5 flex flex-wrap gap-1 text-[10px] text-[var(--arm-text-muted)]">
              {formatTime(t.at, false)}
              {t.heard && <span title="распознано из речи">🎙</span>}
              {t.method === "cloud" && t.role === "caller" && (
                <span title="облачный голос: задержка ответа от конца вашей фразы" data-testid="turn-latency">
                  ☁ {t.latency_ms != null ? `${(t.latency_ms / 1000).toFixed(1)} с` : ""}
                </span>
              )}
              {(t.topics ?? []).map((topic) => (
                <span key={topic} className="rounded-sm bg-[var(--arm-panel)] px-1">
                  {topicTitle(dialog, topic)}
                </span>
              ))}
              {t.audio_url && !telephony && (
                <button type="button" className="underline-offset-2 hover:underline" onClick={() => replay(t.audio_url!)}>
                  ▶ прослушать
                </button>
              )}
            </span>
          </li>
        ))}
      </ol>

      {active && input && (
        <div className="flex flex-col gap-1">
          {dialog.mode === "buttons" && (
            <div className="flex flex-wrap gap-1" aria-label="Темы вопросов">
              {dialog.topics
                .filter((t) => !["repeat", "unknown"].includes(t.code))
                .map((t) => (
                  <button
                    key={t.code}
                    type="button"
                    onClick={() => ask(t.code)}
                    disabled={busy}
                    className="rounded-sm border border-[#a9adb2] bg-white px-1.5 py-0.5 text-[11px] hover:border-[var(--arm-blue)] disabled:opacity-50"
                  >
                    {t.title}
                  </button>
                ))}
            </div>
          )}
          {!telephony && (
            <div className="flex gap-1">
              <input
                aria-label="Ваша реплика"
                placeholder="Скажите заявителю…"
                value={text}
                disabled={busy}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.ctrlKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                className="h-8 min-w-0 flex-1 rounded-sm border border-[#a9adb2] bg-white px-2 text-sm focus:border-[var(--arm-blue)] focus:outline-none"
              />
              <ArmButton variant="blue" className="h-8 w-8 px-0" aria-label="Отправить реплику" onClick={send} disabled={busy || !text.trim()}>
                <Send className="size-4" />
              </ArmButton>
              {dialog.stt_available && typeof MediaRecorder !== "undefined" && (
                <ArmButton
                  variant={recording ? "orange" : "light"}
                  className="h-8 w-8 px-0"
                  aria-label={recording ? "Закончить говорить" : "Говорить в микрофон"}
                  aria-pressed={recording}
                  onClick={() => void toggleMic()}
                  disabled={busy}
                >
                  {recording ? <Circle className="size-4 fill-current" /> : <Mic className="size-4" />}
                </ArmButton>
              )}
            </div>
          )}
          {error && (
            <p className="text-[11px] text-[var(--arm-red)]" role="alert">
              {error instanceof NetworkError ? `${error.message} Отправьте ещё раз: вопрос не задастся дважды.` : error.message}
            </p>
          )}
        </div>
      )}

      {hints && (
        <div className="rounded-sm bg-[var(--arm-field)] p-2 text-xs" data-testid="topics-hint">
          <div className="mb-1 flex justify-between text-[var(--arm-text-muted)]">
            <span>Что выяснить</span>
            <span>
              {covered} / {required.length}
            </span>
          </div>
          <ul className="grid grid-cols-2 gap-x-2 gap-y-0.5">
            {required.map((t) => (
              <li key={t.code} className={cn("flex items-center gap-1", t.covered ? "text-[var(--arm-green)]" : "")} data-covered={t.covered}>
                {t.covered ? <Check className="size-3" aria-hidden /> : <Circle className="size-2.5" aria-hidden />}
                {t.title}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );

  /** Media files need the bearer token, which an <audio src> cannot carry: fetch → blob. */
  async function play(url: string) {
    const el = audio.current;
    if (!el) return;
    try {
      const res = await fetch(url, { headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` } });
      if (!res.ok) return;
      const blob = await res.blob();
      if (el.src.startsWith("blob:")) URL.revokeObjectURL(el.src);
      el.src = URL.createObjectURL(blob);
      await el.play();
      setSpeaking(true);
    } catch {
      setSpeaking(false);
    }
  }

  function replay(url: string) {
    void play(url);
  }
}

function topicTitle(dialog: DialogOut, code: string): string {
  return dialog.topics.find((t) => t.code === code)?.title ?? code;
}
