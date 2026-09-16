import { Phone, PhoneOff } from "lucide-react";

import { ArmButton } from "@/emulator/widgets";
import { CallPanel as SoftphonePanel } from "@/softphone/call-panel";
import { STATUS_LABELS, type Softphone } from "@/softphone/context";

/**
 * The call block at the top-left of the operator card (screenshot page 15). While a call
 * rings, goes on or has just ended it is the softphone panel of `src/softphone/` (SIP through
 * Asterisk or the browser microphone); otherwise the idle block «не подключен / готов».
 */
export function CallBlock({ phone }: { phone: Softphone | null }) {
  if (phone && phone.status !== "disconnected" && phone.status !== "ready") {
    return (
      <SoftphonePanel
        compact
        className="static right-auto bottom-auto z-auto w-[20rem] shrink-0 rounded-none border-0 bg-[var(--arm-panel)] p-2 text-[var(--arm-text)] shadow-none"
      />
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
