import { Phone, PhoneIncoming, PhoneOff } from "lucide-react";

import { ArmButton } from "@/emulator/widgets";
import { END_REASON_LABELS, STATUS_LABELS, type Softphone } from "@/softphone/context";
import { cn } from "@/lib/utils";

/**
 * The call block at the top-left of the operator card (screenshot page 15): one row, as flat
 * as the live one. While a call rings, goes on or has just ended it shows the state and
 * «Ответить» / «Завершить»; the talk controls live in the trainer panel (CallControls).
 * Otherwise the idle block «не подключен / готов».
 */
export function CallBlock({ phone }: { phone: Softphone | null }) {
  if (phone && phone.status !== "disconnected" && phone.status !== "ready") {
    const incoming = phone.status === "incoming";
    const talking = phone.status === "talking";
    const ended = phone.status === "ended";
    return (
      <div
        className="flex h-12 shrink-0 items-center gap-2 bg-[var(--arm-panel)] px-2"
        data-testid="call-panel"
        data-status={phone.status}
        aria-live="polite"
      >
        {incoming ? (
          <PhoneIncoming className="size-5 animate-pulse text-[var(--arm-orange)]" aria-hidden />
        ) : (
          <Phone className={cn("size-5", talking ? "text-[var(--arm-green)]" : "text-[var(--arm-text-muted)]")} aria-hidden />
        )}
        <div className="flex flex-col leading-tight">
          <span className="text-sm">
            Вызов {phone.callerNumber && <span className="font-mono tabular-nums">{phone.callerNumber}</span>}
          </span>
          <span className="text-[10px] text-[var(--arm-text-muted)]" role="status">
            {STATUS_LABELS[phone.status]}
            {ended && phone.endReason ? `: ${END_REASON_LABELS[phone.endReason] ?? phone.endReason}` : ""}
            {phone.error ? ` · ${phone.error}` : ""}
          </span>
        </div>
        <div className="ml-1 flex gap-1">
          {incoming && (
            <ArmButton variant="blue" className="h-7 px-2 normal-case" onClick={() => void phone.answer()} disabled={phone.busy} data-testid="call-answer">
              Ответить
            </ArmButton>
          )}
          {(incoming || talking) && (
            <ArmButton variant="orange" className="h-7 px-2 normal-case" onClick={() => void phone.hangup()} disabled={phone.busy} data-testid="call-hangup">
              Завершить
            </ArmButton>
          )}
          {ended && (
            <ArmButton className="h-7 px-2 normal-case" onClick={phone.dismiss}>
              Закрыть
            </ArmButton>
          )}
        </div>
      </div>
    );
  }
  const ready = phone?.status === "ready";
  const Icon = ready ? Phone : PhoneOff;
  return (
    <div className="flex items-center gap-2 bg-[var(--arm-panel)] px-2 py-1" data-testid="call-panel" data-status={ready ? "ready" : "disconnected"}>
      <Icon className={ready ? "size-5 text-[var(--arm-green)]" : "size-5 text-[var(--arm-text-muted)]"} aria-hidden />
      <div className="flex flex-col gap-1">
        <span className="text-sm text-[var(--arm-text-muted)]" role="status">
          {phone ? STATUS_LABELS[phone.status] : "не подключен"}
          {phone?.mode === "browser" && <span className="ml-1 text-[10px]">(без телефонии)</span>}
        </span>
        <div className="flex gap-1">
          <ArmButton className="h-5 px-1.5 text-[9px] normal-case" disabled>
            записи звонков
          </ArmButton>
          <ArmButton className="h-5 px-1.5 text-[9px] normal-case" disabled>
            список SMS
          </ArmButton>
        </div>
      </div>
    </div>
  );
}
