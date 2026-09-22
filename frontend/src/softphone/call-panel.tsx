import {
  Cloud,
  Mic,
  MicOff,
  Phone,
  PhoneIncoming,
  PhoneOff,
  Send,
} from "lucide-react";
import { useState } from "react";

import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  END_REASON_LABELS,
  STATUS_LABELS,
  useSoftphone,
  type CallStatus,
} from "@/softphone/context";

const STATUS_TONE: Record<CallStatus, BadgeTone> = {
  disconnected: "neutral",
  ready: "success",
  incoming: "warning",
  talking: "primary",
  ended: "neutral",
};

/** Small state chip for the cabinet header. */
export function SoftphoneBadge() {
  const phone = useSoftphone();
  if (!phone) return null;
  const title =
    phone.mode === "sip"
      ? phone.registration === "failed"
        ? `Софтфон: ${phone.registrationDetail ?? "ошибка регистрации"}`
        : "Софтфон: звонки через Asterisk"
      : "Софтфон: через микрофон браузера (телефония выключена)";
  return (
    <Badge
      tone={STATUS_TONE[phone.status]}
      title={title}
      data-testid="softphone-status"
    >
      <Phone className="mr-1 size-3" aria-hidden />
      софтфон: {STATUS_LABELS[phone.status]}
    </Badge>
  );
}

/** Microphone level, 0..1. */
function LevelMeter({ level }: { level: number }) {
  return (
    <div
      className="flex items-center gap-1"
      title="Уровень микрофона"
      aria-label="Уровень микрофона"
    >
      <Mic className="size-3.5 text-muted-foreground" aria-hidden />
      <div className="h-1.5 w-20 overflow-hidden rounded bg-muted">
        <div
          className={cn(
            "h-full transition-[width] duration-100",
            level > 0.7 ? "bg-destructive" : "bg-success",
          )}
          style={{ width: `${Math.round(level * 100)}%` }}
        />
      </div>
    </div>
  );
}

/**
 * The call panel of the trainee (PRD 9.5): state, answer / hang up, «нет контакта»,
 * «срыв звонка», microphone level and device, the caller's last words. Floats over every
 * page of the trainee while a call rings or goes on; the operator card (wave 7) embeds it
 * with `compact` (no scenario title, the card shows the transcript itself).
 */
export function CallPanel({
  className,
  compact = false,
}: {
  className?: string;
  compact?: boolean;
}) {
  const phone = useSoftphone();
  if (!phone) return null;
  // A call to a service officer lives in the card (issue #36), not in this panel.
  if (phone.serviceCallId) return null;
  if (phone.status === "disconnected" || phone.status === "ready") return null;

  const talking = phone.status === "talking";
  const incoming = phone.status === "incoming";
  const ended = phone.status === "ended";

  return (
    <section
      className={cn(
        "fixed right-4 bottom-4 z-40 w-[min(26rem,calc(100vw-2rem))] rounded-lg border bg-card p-3 shadow-lg",
        className,
      )}
      aria-live="polite"
      data-testid="call-panel"
    >
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          {incoming ? (
            <PhoneIncoming
              className="size-4 animate-pulse text-warning"
              aria-hidden
            />
          ) : (
            <Phone className="size-4" aria-hidden />
          )}
          Вызов{" "}
          {phone.callerNumber && (
            <span className="font-mono">{phone.callerNumber}</span>
          )}
        </div>
        <Badge tone={STATUS_TONE[phone.status]}>
          {STATUS_LABELS[phone.status]}
        </Badge>
      </header>
      {/* The operator card hides the scenario title: the trainee must not see what the call is about. */}
      {phone.scenarioTitle && !compact && (
        <div className="mt-1 text-xs text-muted-foreground">
          {phone.scenarioTitle}
        </div>
      )}

      {phone.lastCaller && (
        <div className="mt-2 rounded-md bg-muted/60 px-2 py-1.5 text-sm">
          {phone.lastCaller.heardText && (
            <div className="text-xs text-muted-foreground">
              Вы: {phone.lastCaller.heardText}
            </div>
          )}
          <div>Заявитель: {phone.lastCaller.text}</div>
        </div>
      )}
      {ended && (
        <div className="mt-2 text-sm text-muted-foreground">
          Звонок завершён
          {phone.endReason
            ? `: ${END_REASON_LABELS[phone.endReason] ?? phone.endReason}`
            : ""}
          .
        </div>
      )}
      {phone.error && (
        <div role="alert" className="mt-2 text-sm text-destructive">
          {phone.error}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {incoming && (
          <Button
            size="sm"
            onClick={() => void phone.answer()}
            disabled={phone.busy}
            data-testid="call-answer"
          >
            <Phone /> Ответить
          </Button>
        )}
        {(incoming || talking) && (
          <>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => void phone.hangup()}
              disabled={phone.busy}
              data-testid="call-hangup"
            >
              <PhoneOff /> Завершить
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void phone.noContact()}
              disabled={phone.busy}
              title="Заявитель недоступен"
            >
              Нет контакта
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void phone.callDropped()}
              disabled={phone.busy}
              title="Заявитель бросил трубку"
            >
              Срыв звонка
            </Button>
          </>
        )}
        {ended && (
          <Button size="sm" variant="outline" onClick={phone.dismiss}>
            Закрыть
          </Button>
        )}
      </div>

      <CallControls />
    </section>
  );
}

/**
 * The controls of a call in progress: the cloud state, push-to-talk / text to the caller,
 * the microphone. Part of the floating panel; the operator card shows them in the trainer
 * panel next to the transcript, keeping the header row of the card as flat as the live one.
 */
export function CallControls({ className }: { className?: string }) {
  const phone = useSoftphone();
  const [text, setText] = useState("");
  if (!phone || phone.serviceCallId) return null;
  if (phone.status === "disconnected" || phone.status === "ready") return null;
  const talking = phone.status === "talking";
  return (
    <div className={className} data-testid="call-controls">
      {talking && phone.mode === "browser" && phone.cloud === "connecting" && (
        <div
          className="mt-3 flex items-center gap-2 text-sm text-muted-foreground"
          role="status"
          data-testid="cloud-connecting"
        >
          <Cloud className="size-4 animate-pulse" aria-hidden /> Соединяем с
          облачным заявителем…
        </div>
      )}
      {talking && phone.mode === "browser" && phone.cloud === "live" && (
        <div className="mt-3 space-y-2" data-testid="cloud-live">
          <div
            className="flex items-center gap-2 text-sm"
            role="status"
            aria-live="polite"
          >
            <Cloud className="size-4 text-primary" aria-hidden />
            {phone.callerSpeaking
              ? "Заявитель говорит…"
              : "Заявитель слушает: говорите свободно, можно перебивать."}
          </div>
          <Button
            size="sm"
            variant={phone.muted ? "destructive" : "secondary"}
            className="w-full"
            onClick={() => phone.setMuted(!phone.muted)}
            aria-pressed={phone.muted}
            data-testid="cloud-mute"
          >
            {phone.muted ? <MicOff /> : <Mic />}{" "}
            {phone.muted ? "Микрофон выключен" : "Микрофон включён"}
          </Button>
        </div>
      )}
      {talking &&
        phone.mode === "browser" &&
        (phone.cloud === "off" || phone.cloud === "failed") && (
        <div className="mt-3 space-y-2">
          {phone.sttAvailable ? (
            <Button
              size="sm"
              variant={phone.recording ? "destructive" : "secondary"}
              className="w-full"
              onPointerDown={() => void phone.startRecording()}
              onPointerUp={() => void phone.stopRecording()}
              onPointerLeave={() => void phone.stopRecording()}
              disabled={phone.busy}
              data-testid="call-talk"
            >
              <Mic />{" "}
              {phone.recording
                ? "Говорите… отпустите, чтобы отправить"
                : "Удерживайте и говорите"}
            </Button>
          ) : (
            <div className="text-xs text-muted-foreground">
              Распознавание речи недоступно: говорите текстом.
            </div>
          )}
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              const phrase = text.trim();
              if (!phrase) return;
              setText("");
              void phone.sayText(phrase);
            }}
          >
            <Input
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Фраза заявителю текстом"
              aria-label="Фраза заявителю"
            />
            <Button
              type="submit"
              size="sm"
              variant="outline"
              disabled={phone.busy || !text.trim()}
              aria-label="Отправить"
            >
              <Send />
            </Button>
          </form>
        </div>
      )}

      <footer className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <LevelMeter level={phone.micLevel} />
        {phone.devices.length > 0 && (
          <select
            className="max-w-[12rem] rounded border bg-background px-1 py-0.5"
            value={phone.micDeviceId ?? ""}
            onChange={(e) => phone.setMicDevice(e.target.value || null)}
            aria-label="Микрофон"
            title="Микрофон"
          >
            <option value="">Микрофон по умолчанию</option>
            {phone.devices.map((d, i) => (
              <option key={d.deviceId || i} value={d.deviceId}>
                {d.label || `Микрофон ${i + 1}`}
              </option>
            ))}
          </select>
        )}
        {phone.stats && (
          <span
            className="font-mono"
            title="Статистика WebRTC"
            data-testid="call-stats"
          >
            {phone.stats.codec ?? "—"} · RTT {phone.stats.rttMs ?? "—"} мс ·
            джиттер {phone.stats.jitterMs ?? "—"} мс
            {phone.stats.packetsLost
              ? ` · потери ${phone.stats.packetsLost}`
              : ""}
          </span>
        )}
      </footer>
    </div>
  );
}
