import { AlertTriangle, Check, Clock3, PanelRightClose, PanelRightOpen, Wifi, WifiOff } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { acceptanceTimer, formatSeconds } from "@/emulator/time";
import type { ConnectionState } from "@/emulator/ws";
import { cn } from "@/lib/utils";

/** Colour of the acceptance timer by phase (plan wave 3: normal → warning → red + icon). */
const PHASE_CLASS: Record<ReturnType<typeof acceptanceTimer>["phase"], string> = {
  ok: "text-[var(--arm-on-dark)]",
  warning: "text-[var(--arm-warning)]",
  overdue: "text-[var(--arm-red)]",
  done: "text-[var(--arm-green)]",
  late: "text-[var(--arm-red)]",
};

export function TimerBadge({
  issuedAt,
  primaryStatusAt,
  normSeconds,
  now,
  active,
  className,
}: {
  issuedAt: string;
  primaryStatusAt: string | null | undefined;
  normSeconds: number;
  now: number;
  active: boolean;
  className?: string;
}) {
  if (!active && !primaryStatusAt) {
    return <span className={cn("text-[var(--arm-on-dark-muted)]", className)}>—</span>;
  }
  const t = acceptanceTimer(issuedAt, primaryStatusAt, normSeconds, now);
  const text = primaryStatusAt ? formatSeconds(t.elapsed) : formatSeconds(t.remaining);
  const label =
    t.phase === "done"
      ? `Первичный статус за ${text}`
      : t.phase === "late"
        ? `Первичный статус с опозданием, ${text}`
        : t.phase === "overdue"
          ? `Норматив ${normSeconds} с превышен на ${formatSeconds(-t.remaining)}`
          : `До конца норматива ${text}`;
  return (
    <span
      className={cn("inline-flex items-center gap-1 font-mono text-sm tabular-nums", PHASE_CLASS[t.phase], className)}
      title={label}
      aria-label={label}
      data-phase={t.phase}
    >
      {t.phase === "overdue" || t.phase === "late" ? (
        <AlertTriangle className="size-3.5" aria-hidden />
      ) : t.phase === "done" ? (
        <Check className="size-3.5" aria-hidden />
      ) : (
        <Clock3 className="size-3.5" aria-hidden />
      )}
      {text}
    </span>
  );
}

export function ConnectionBadge({ state }: { state: ConnectionState }) {
  if (state === "online") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-[var(--arm-green)]" role="status">
        <Wifi className="size-3.5" aria-hidden /> связь есть
      </span>
    );
  }
  return (
    <span
      className={cn("inline-flex items-center gap-1 text-xs", state === "offline" ? "text-[var(--arm-red)]" : "text-[var(--arm-text-muted)]")}
      role="status"
    >
      <WifiOff className="size-3.5" aria-hidden />
      {state === "offline" ? "связь потеряна, переподключаемся…" : "подключаемся…"}
    </span>
  );
}

/** Thin right-hand training panel: what is not part of the real workstation (PRD 13.1). */
export function TrainerPanel({
  connection,
  children,
  footer,
  width = "default",
}: {
  connection: ConnectionState;
  children: ReactNode;
  footer?: ReactNode;
  /** «wide» holds the conversation panel of the operator card. */
  width?: "default" | "wide";
}) {
  const [collapsed, setCollapsed] = useState(false);
  if (collapsed) {
    return (
      <aside className="flex w-9 shrink-0 flex-col items-center gap-3 border-l border-[#a9adb2] bg-[var(--arm-panel)] py-2" aria-label="Тренажёр">
        <button type="button" aria-label="Развернуть панель тренажёра" onClick={() => setCollapsed(false)} className="rounded-sm p-1 hover:bg-[var(--arm-panel-2)]">
          <PanelRightOpen className="size-4" />
        </button>
        <span className="text-[10px] font-semibold uppercase tracking-wide text-[var(--arm-text-muted)] [writing-mode:vertical-rl]">Тренажёр</span>
        <span className={cn("size-2 rounded-full", connection === "online" ? "bg-[var(--arm-green)]" : "bg-[var(--arm-red)]")} title={connection === "online" ? "связь есть" : "связь потеряна"} />
      </aside>
    );
  }
  return (
    <aside
      className={cn("flex shrink-0 flex-col gap-3 border-l border-[#a9adb2] bg-[var(--arm-panel)] p-3 text-sm", width === "wide" ? "w-80" : "w-64")}
      aria-label="Тренажёр"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold uppercase tracking-wide text-[var(--arm-text-muted)]">Тренажёр</span>
        <ConnectionBadge state={connection} />
        <button type="button" aria-label="Свернуть панель тренажёра" onClick={() => setCollapsed(true)} className="rounded-sm p-1 hover:bg-[var(--arm-panel-2)]">
          <PanelRightClose className="size-4" />
        </button>
      </div>
      <div className="flex flex-1 flex-col gap-3">{children}</div>
      <div className="flex flex-col gap-2 border-t border-[#a9adb2] pt-3">
        {footer}
        <Link to="/student" className="text-xs text-[var(--arm-blue-dark)] underline-offset-2 hover:underline">
          В кабинет обучающегося
        </Link>
      </div>
    </aside>
  );
}

export function ArmButton({
  className,
  variant = "light",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "light" | "dark" | "orange" | "blue" }) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex h-8 items-center justify-center gap-1 rounded-sm px-3 text-xs uppercase tracking-wide transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--arm-blue)] disabled:cursor-not-allowed disabled:opacity-50",
        variant === "light" && "bg-[var(--arm-panel-2)] text-[var(--arm-text)] hover:bg-[#cfd2d4]",
        variant === "dark" && "bg-[var(--arm-dark)] text-[var(--arm-on-dark)] hover:bg-[var(--arm-dark-2)]",
        variant === "orange" && "bg-[var(--arm-orange)] font-semibold text-white hover:bg-[#d95a1e]",
        variant === "blue" && "bg-[var(--arm-blue)] text-white hover:bg-[var(--arm-blue-dark)]",
        className,
      )}
      {...props}
    />
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <label className={cn("inline-flex cursor-pointer items-center gap-2 text-xs", disabled && "cursor-not-allowed opacity-60")}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-4 w-8 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--arm-blue)]",
          checked ? "bg-[var(--arm-blue)]" : "bg-[#7d838a]",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 size-3 rounded-full bg-white transition-transform",
            checked ? "translate-x-4" : "translate-x-0.5",
          )}
        />
      </button>
      <span>{label}</span>
    </label>
  );
}
