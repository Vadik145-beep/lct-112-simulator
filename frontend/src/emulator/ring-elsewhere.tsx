import { PhoneIncoming, X } from "lucide-react";
import { useState } from "react";

import type { RingingOut } from "@/api/training";
import { cn } from "@/lib/utils";

/**
 * A squad calling about another card of the trainee (#263, стенд 18.10.2026: the report about
 * card A was missed while the trainee sat in card B). Orange, not red: it is not this card's
 * call, and the trainee must see which card it is before going there.
 */
export function OtherCardCalls({
  calls,
  onGo,
  title = "Звонок по другой карточке",
}: {
  calls: RingingOut[];
  onGo: (call: RingingOut) => void;
  /** In the journal no card is open: «Звонит бригада по карточке». */
  title?: string;
}) {
  if (calls.length === 0) return null;
  return (
    <div className="flex flex-col gap-1" data-testid="other-card-calls">
      {calls.map((c) => (
        <div
          key={c.call_id}
          className="flex flex-col gap-1 rounded-sm border border-[var(--arm-orange)] bg-[#fff1ea] p-2 text-xs"
        >
          <span className="font-semibold text-[var(--arm-orange)]">
            {title} № {c.card_number}
          </span>
          <span>
            {c.incident_title}
            {c.address ? `, ${c.address}` : ""}
          </span>
          <button
            type="button"
            onClick={() => onGo(c)}
            className="self-start rounded-sm bg-[var(--arm-orange)] px-2 py-1 font-semibold text-white hover:brightness-110"
          >
            Перейти в карточку и ответить
          </button>
        </div>
      ))}
    </div>
  );
}

/** The phone circle of the journal: there is no card to dial from, it only rings. */
export function JournalPhone({
  calls,
  onGo,
}: {
  calls: RingingOut[];
  onGo: (call: RingingOut) => void;
}) {
  const [open, setOpen] = useState(false);
  if (calls.length === 0) return null;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-label="Телефон: звонок по карточке"
        title="Звонок по карточке"
        data-testid="journal-phone"
        className={cn(
          "fixed bottom-[72px] right-[calc(var(--trainer-panel-width,36px)_+_12px)] z-40 flex size-14 items-center justify-center rounded-full bg-[var(--arm-green)] text-white shadow-lg ring-4 ring-[var(--arm-orange)] hover:brightness-110",
        )}
      >
        <PhoneIncoming className="size-6" aria-hidden />
      </button>
      {open && (
        <section
          aria-label="Входящие звонки"
          className="fixed bottom-[140px] right-[calc(var(--trainer-panel-width,36px)_+_12px)] z-40 w-[320px] overflow-hidden rounded-md border border-[var(--arm-dark)] bg-[var(--arm-panel)] text-[var(--arm-text)] shadow-xl"
        >
          <header className="flex items-center justify-between bg-[var(--arm-dark)] px-3 py-2 text-sm text-[var(--arm-on-dark)]">
            <span className="font-medium">Телефон</span>
            <button type="button" onClick={() => setOpen(false)} aria-label="Свернуть телефон">
              <X className="size-4" aria-hidden />
            </button>
          </header>
          <div className="p-2">
            <OtherCardCalls calls={calls} onGo={onGo} title="Звонит бригада по карточке" />
          </div>
        </section>
      )}
    </>
  );
}
