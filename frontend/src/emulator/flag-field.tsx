import { Flag, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ServiceStatusOut } from "@/api/training";
import { FIELD_TITLES, describeCorrection, serviceCorrection, type FlagRequest, type FlaggedField } from "@/emulator/flag-field-model";
import { cn } from "@/lib/utils";

/** Small flag button next to a field of the card. */
export function FlagButton({
  field,
  flagged,
  disabled,
  onClick,
  className,
}: {
  field: string;
  flagged: boolean;
  disabled?: boolean;
  onClick: () => void;
  className?: string;
}) {
  const title = FIELD_TITLES[field] ?? field;
  return (
    <button
      type="button"
      aria-label={flagged ? `Ошибка отмечена: ${title}` : `Отметить ошибку: ${title}`}
      aria-pressed={flagged}
      title={flagged ? "Ошибка отмечена. Нажмите, чтобы изменить или снять отметку" : "Отметить ошибку оператора 112 в этом поле"}
      disabled={disabled}
      onClick={onClick}
      data-field={field}
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-sm border text-[var(--arm-text-muted)] hover:bg-[var(--arm-panel-2)] disabled:cursor-not-allowed disabled:opacity-40",
        flagged ? "border-[var(--arm-orange)] bg-[var(--arm-orange)] text-white hover:bg-[#d95a1e]" : "border-transparent",
        className,
      )}
    >
      <Flag className="size-3.5" aria-hidden />
    </button>
  );
}

/** A flagged field shows its correction under the value. */
export function FlagMark({ flag, services, onRemove, disabled }: { flag: FlaggedField; services: ServiceStatusOut[]; onRemove: () => void; disabled?: boolean }) {
  return (
    <div className="flex items-center gap-1 text-[11px] text-[var(--arm-orange)]" data-testid="flag-mark" data-field={flag.field}>
      <Flag className="size-3" aria-hidden />
      <span>
        Ошибка: {flag.title.toLowerCase()} — {describeCorrection(flag.field, flag.corrected_value, services)}
      </span>
      {!disabled && (
        <button type="button" aria-label={`Снять отметку: ${flag.title}`} onClick={onRemove} className="rounded-sm p-0.5 hover:bg-[#fff1ea]">
          <X className="size-3" aria-hidden />
        </button>
      )}
    </div>
  );
}

/** The inline editor of a flag: what the field should be. */
export function FlagEditor({
  field,
  current,
  services,
  addressParts,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  /** Field to flag; for the address the part is chosen inside the editor. */
  field: string;
  current: FlaggedField | undefined;
  services: ServiceStatusOut[];
  /** Address parts present on the card (only those can be wrong). */
  addressParts: { key: string; title: string; value: string }[];
  pending: boolean;
  error: string | null;
  onSubmit: (request: FlagRequest) => void;
  onCancel: () => void;
}) {
  const address = field === "address";
  const [part, setPart] = useState(current?.field.startsWith("address.") ? current.field : addressParts[0] ? `address.${addressParts[0].key}` : "address.house");
  const [value, setValue] = useState(current && !current.field.startsWith("address.") ? current.corrected_value : "");
  const [serviceKind, setServiceKind] = useState<"extra" | "missing">(current?.corrected_value.startsWith("+") ? "missing" : "extra");
  const [serviceCode, setServiceCode] = useState(current?.field === "services" ? current.corrected_value.slice(1) : "");
  const [injured, setInjured] = useState(current?.field === "flags.injured" ? current.corrected_value : "false");
  const first = useRef<HTMLElement>(null);
  useEffect(() => {
    first.current?.focus();
  }, []);

  const targetField = address ? part : field;
  const title = FIELD_TITLES[targetField] ?? targetField;
  const others = services.filter((s) => !s.is_own);

  function submit() {
    if (targetField === "services") {
      const code = serviceCode.trim();
      if (!code) return;
      onSubmit({ field: "services", corrected_value: serviceCorrection(serviceKind, code) });
    } else if (targetField === "flags.injured") {
      onSubmit({ field: targetField, corrected_value: injured });
    } else {
      if (!value.trim()) return;
      onSubmit({ field: targetField, corrected_value: value.trim() });
    }
  }

  const inputClass = "h-7 border-b border-[#a9adb2] bg-white px-2 text-xs focus:border-[var(--arm-blue)] focus:outline-none";
  return (
    <form
      role="form"
      aria-label={`Ошибка в поле: ${title}`}
      data-testid="flag-editor"
      className="flex flex-wrap items-center gap-1 rounded-sm border border-[var(--arm-orange)] bg-[#fff1ea] p-1 text-xs"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <span className="font-semibold text-[var(--arm-orange)]">Ошибка оператора 112:</span>
      {address && (
        <select ref={first as React.RefObject<HTMLSelectElement>} aria-label="Часть адреса" value={part} onChange={(e) => setPart(e.target.value)} className={inputClass}>
          {addressParts.map((p) => (
            <option key={p.key} value={`address.${p.key}`}>
              {p.title}: {p.value}
            </option>
          ))}
        </select>
      )}
      {targetField === "services" ? (
        <>
          <select ref={address ? undefined : (first as React.RefObject<HTMLSelectElement>)} aria-label="Что не так со службами" value={serviceKind} onChange={(e) => setServiceKind(e.target.value as "extra" | "missing")} className={inputClass}>
            <option value="extra">служба лишняя</option>
            <option value="missing">службы не хватает</option>
          </select>
          {serviceKind === "extra" ? (
            <select aria-label="Лишняя служба" value={serviceCode} onChange={(e) => setServiceCode(e.target.value)} className={inputClass}>
              <option value="">выберите службу…</option>
              {others.map((s) => (
                <option key={s.code} value={s.code}>
                  {s.title}
                </option>
              ))}
            </select>
          ) : (
            <input aria-label="Какой службы не хватает" placeholder="служба (код или название)" value={serviceCode} onChange={(e) => setServiceCode(e.target.value)} className={cn(inputClass, "w-56")} />
          )}
        </>
      ) : targetField === "flags.injured" ? (
        <select ref={first as React.RefObject<HTMLSelectElement>} aria-label="Пострадавшие на самом деле" value={injured} onChange={(e) => setInjured(e.target.value)} className={inputClass}>
          <option value="false">пострадавших нет</option>
          <option value="true">есть пострадавшие</option>
        </select>
      ) : (
        <input
          ref={address ? undefined : (first as React.RefObject<HTMLInputElement>)}
          aria-label={`Правильное значение: ${title}`}
          placeholder="как должно быть"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className={cn(inputClass, "w-56")}
        />
      )}
      <button type="submit" disabled={pending} className="h-7 rounded-sm bg-[var(--arm-orange)] px-2 font-semibold text-white hover:bg-[#d95a1e] disabled:opacity-50">
        Отметить
      </button>
      <button type="button" onClick={onCancel} className="h-7 rounded-sm border border-[#a9adb2] px-2 hover:bg-white">
        Отмена
      </button>
      {error && (
        <span role="alert" className="w-full text-[var(--arm-red)]">
          {error}
        </span>
      )}
    </form>
  );
}
