import { MapPin } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { useStreets, type AddressIn } from "@/api/intake";
import { addressLine, EMPTY_ADDRESS } from "@/intake/draft";
import { cn } from "@/lib/utils";

type Address = Required<AddressIn>;

const MOSCOW = "Москва";

export function AddressForm({
  address,
  onChange,
  disabled,
}: {
  address: Address;
  onChange: (next: Address) => void;
  disabled?: boolean | undefined;
}) {
  const set = (key: keyof Address) => (e: React.ChangeEvent<HTMLInputElement>) => onChange({ ...address, [key]: e.target.value });
  const isMoscow = !address.region || address.region === MOSCOW;

  return (
    <div className="flex flex-col gap-1 bg-[var(--arm-panel)] px-3 py-2" data-testid="address-form">
      <div className="flex items-center gap-2">
        <span className="text-xs text-[var(--arm-text-muted)]">Адрес:</span>
        <MapPin className="size-3.5 text-[var(--arm-text-muted)]" aria-hidden />
        <span className="flex-1 truncate text-sm font-medium" data-testid="address-line">
          {addressLine(address)}
        </span>
        <button
          type="button"
          onClick={() => onChange({ ...EMPTY_ADDRESS })}
          disabled={disabled}
          className="rounded-sm border border-[#a9adb2] bg-white px-2 py-0.5 text-[11px] hover:bg-[var(--arm-panel-2)] disabled:opacity-50"
        >
          очистить адрес
        </button>
      </div>
      <div className="grid grid-cols-3 gap-x-3 gap-y-1">
        <Field label="Страна" value="Россия" readOnly />
        <Field label="Субъект" value={address.region || MOSCOW} onChange={(e) => onChange({ ...address, region: e.target.value === MOSCOW ? "" : e.target.value })} disabled={disabled} title="Регион: пустое = Москва" />
        <Field label="Населённый пункт" value={address.city || (isMoscow ? MOSCOW : "")} onChange={(e) => onChange({ ...address, city: e.target.value === MOSCOW && isMoscow ? "" : e.target.value })} disabled={disabled} />
        <Field label="Объект" value={address.object} onChange={set("object")} disabled={disabled} className="col-span-1" />
        <Field label="Округ" value={address.okrug} onChange={set("okrug")} disabled={disabled} />
        <Field label="Район" value={address.district} onChange={set("district")} disabled={disabled} />
        <StreetField address={address} onChange={onChange} disabled={disabled} />
        <Field label="Дом/Вл." value={address.house} onChange={set("house")} disabled={disabled} />
        <Field label="Корпус" value={address.building} onChange={set("building")} disabled={disabled} />
        <div className="col-span-3 grid grid-cols-5 gap-x-3">
          <Field label="Стр/соор." value={address.structure} onChange={set("structure")} disabled={disabled} />
          <Field label="Квартира/офис" value={address.apartment} onChange={set("apartment")} disabled={disabled} />
          <Field label="Подъезд" value={address.entrance} onChange={set("entrance")} disabled={disabled} />
          <Field label="Этаж" value={address.floor} onChange={set("floor")} disabled={disabled} />
          <Field label="Код" value={address.code} onChange={set("code")} disabled={disabled} />
        </div>
        <Field label="Описательный адрес" value={address.descriptive} onChange={set("descriptive")} disabled={disabled} className="col-span-3" />
      </div>
    </div>
  );
}

function Field({
  label,
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  const id = useId();
  return (
    <div className={cn("flex flex-col", className)}>
      <label htmlFor={id} className="text-[10px] text-[var(--arm-text-muted)]">
        {label}
      </label>
      <input
        id={id}
        {...props}
        className={cn(
          "h-6 border-b border-[#a9adb2] bg-transparent text-sm focus:border-[var(--arm-blue)] focus:outline-none disabled:text-[var(--arm-text-muted)]",
          props.readOnly && "text-[var(--arm-text-muted)]",
        )}
      />
    </div>
  );
}

/** Street with hints from `/streets` (PRD 13.5): picking one fills okrug and district. */
function StreetField({ address, onChange, disabled }: { address: Address; onChange: (next: Address) => void; disabled?: boolean | undefined }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState(false);
  const hints = useStreets(typed ? address.street : "");
  const box = useRef<HTMLDivElement>(null);
  const id = useId();
  const listId = `${id}-list`;
  const options = open && typed ? hints.data ?? [] : [];

  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  return (
    <div ref={box} className="relative flex flex-col">
      <label htmlFor={id} className="text-[10px] text-[var(--arm-text-muted)]">
        Улица
      </label>
      <input
        id={id}
        role="combobox"
        aria-expanded={options.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        autoComplete="off"
        value={address.street}
        disabled={disabled}
        onChange={(e) => {
          setTyped(true);
          setOpen(true);
          onChange({ ...address, street: e.target.value });
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
          if (e.key === "Enter" && options[0]) {
            e.preventDefault();
            pick(options[0]);
          }
        }}
        className="h-6 border-b border-[#a9adb2] bg-transparent text-sm focus:border-[var(--arm-blue)] focus:outline-none"
      />
      {options.length > 0 && (
        <ul id={listId} role="listbox" aria-label="Подсказка улиц" className="absolute left-0 top-full z-10 max-h-44 w-80 overflow-y-auto border border-[#a9adb2] bg-white text-xs shadow-lg">
          {options.map((s, i) => (
            <li key={`${s.name}-${s.district}-${i}`} role="option" aria-selected={false}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(s)} className="flex w-full flex-col px-2 py-1 text-left hover:bg-[var(--arm-panel)]">
                <span>{s.name}</span>
                <span className="text-[var(--arm-text-muted)]">
                  {s.okrug}, {s.district}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );

  function pick(s: { name: string; okrug: string; district: string }) {
    setOpen(false);
    setTyped(false);
    onChange({ ...address, street: s.name, okrug: s.okrug, district: s.district, region: "", city: "" });
  }
}
