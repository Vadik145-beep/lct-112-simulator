import { Delete, Phone, PhoneIncoming, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { usePhoneBook } from "@/api/phones";
import { useRinging, type AttemptOut } from "@/api/training";
import { goAndAnswer, type AnswerOnArrival } from "@/emulator/answer-on-arrival";
import { OtherCardCalls } from "@/emulator/ring-elsewhere";
import { ServiceCallPanel } from "@/emulator/service-call";
import { callTitle, describeCall } from "@/emulator/service-call-model";
import type { CardCalls } from "@/emulator/use-card-calls";
import { cn } from "@/lib/utils";

// Statuses at which the squad is out and its reports may still come (issue #103).
const REPORTING_STATUSES = new Set([
  "accepted",
  "response_started",
  "arrived",
  "works_started",
]);
const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"];
const RECENT_CALLS = 5;

// The circle sits 48 px from the right edge — clear of the folded trainer panel (36 px) — and
// above the services strip.
/**
 * The virtual phone of the dispatcher (#263, рекомендации заказчика п. 8): the green circle in
 * the corner of the work panel opens a phone with a keypad, the book of the service from the
 * teacher's directory and the last calls. The number dialled tells the server who answers;
 * the conversation, a squad's incoming report included, goes on in the same window.
 */
export function VirtualPhone({
  attempt,
  calls,
}: {
  attempt: AttemptOut;
  calls: CardCalls;
}) {
  const open = calls.phoneOpen;
  const setOpen = calls.setPhoneOpen;
  const [number, setNumber] = useState("");
  // A call that went through leaves the keypad clean for the next number.
  const openCallId = calls.openCall?.id ?? null;
  useEffect(() => {
    if (openCallId) setNumber("");
  }, [openCallId]);
  const [search, setSearch] = useState("");
  const book = usePhoneBook(attempt.id);
  const { openCall, shownCall } = calls;
  const ringing = Boolean(
    openCall && openCall.kind === "report" && !openCall.answered,
  );
  const ownService = attempt.card.services.find((s) => s.is_own)?.code;
  // A squad calling about another card rings here too, in orange (#263).
  const navigate = useNavigate();
  const ringingNow = useRinging(attempt.session.id);
  const elsewhere = (ringingNow.data ?? []).filter((r) => r.attempt_id !== attempt.id);
  const other = !ringing && elsewhere.length > 0;
  // Came here by «Перейти в карточку и ответить»: the call is picked up as soon as this card
  // shows it ringing, once; a call that stopped ringing meanwhile is left alone.
  const location = useLocation();
  const answerCallId = (location.state as AnswerOnArrival | null)?.answerCallId ?? null;
  const { answer, busy } = calls;
  const awaited = attempt.service_calls.find((c) => c.id === answerCallId);
  const answerNow = Boolean(answerCallId && ringing && openCall?.id === answerCallId && !busy);
  const rangOff = Boolean(answerCallId && awaited && awaited.ended_at !== null);
  const pickedUp = useRef<string | null>(null);
  useEffect(() => {
    if (!answerNow && !rangOff) return;
    navigate(location.pathname, { replace: true, state: null });
    if (!answerNow || pickedUp.current === answerCallId) return;
    pickedUp.current = answerCallId;
    answer();
  }, [answerNow, rangOff, answerCallId, answer, navigate, location.pathname]);

  const entries = useMemo(() => {
    const query = search.trim().toLowerCase();
    const rows = book.data?.entries ?? [];
    if (!query) return rows;
    return rows.filter((e) =>
      [e.role_title, e.name, e.number ?? ""].some((v) =>
        v.toLowerCase().includes(query),
      ),
    );
  }, [book.data, search]);
  const recent = [...attempt.service_calls].reverse().slice(0, RECENT_CALLS);

  const type = (value: string) => {
    calls.resetDialError();
    setNumber(value.replace(/[^\d*#+]/g, "").slice(0, 20));
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (number.trim()) calls.dial(number);
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-label={
          ringing
            ? "Телефон: входящий вызов"
            : other
              ? "Телефон: звонок по другой карточке"
              : "Телефон"
        }
        aria-expanded={open}
        title={ringing ? "Входящий вызов" : other ? "Звонок по другой карточке" : "Телефон"}
        data-testid="virtual-phone-button"
        className={cn(
          "fixed bottom-[72px] right-[calc(var(--trainer-panel-width,36px)_+_12px)] z-40 flex size-14 items-center justify-center rounded-full text-white shadow-lg hover:brightness-110",
          "bg-[var(--arm-green)]",
          ringing && "ring-4 ring-[var(--arm-red)]",
          other && "ring-4 ring-[var(--arm-orange)]",
        )}
      >
        {ringing || other ? (
          <PhoneIncoming className="size-6" aria-hidden />
        ) : (
          <Phone className="size-6" aria-hidden />
        )}
      </button>

      {open && (
        <section
          aria-label="Виртуальный телефон"
          data-testid="virtual-phone"
          className="fixed bottom-[140px] right-[calc(var(--trainer-panel-width,36px)_+_12px)] z-40 flex max-h-[calc(100vh-10rem)] w-[360px] flex-col overflow-hidden rounded-md border border-[var(--arm-dark)] bg-[var(--arm-panel)] text-[var(--arm-text)] shadow-xl"
        >
          <header className="flex items-center justify-between bg-[var(--arm-dark)] px-3 py-2 text-sm text-[var(--arm-on-dark)]">
            <span className="font-medium">Телефон</span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Свернуть телефон"
            >
              <X className="size-4" aria-hidden />
            </button>
          </header>

          <div className="flex flex-col gap-3 overflow-y-auto p-3">
            <OtherCardCalls
              calls={elsewhere}
              onGo={(call) => goAndAnswer(navigate, call)}
            />
            {shownCall ? (
              <>
                {/* The transcript grows into the room it is given: here a fixed height. */}
                <div className="flex h-[380px] shrink-0 flex-col">
                  <ServiceCallPanel
                    call={shownCall}
                    telephony={Boolean(
                      shownCall.telephony && calls.softphone?.mode === "sip",
                    )}
                    sttAvailable={calls.sttAvailable}
                    micDeviceId={calls.softphone?.micDeviceId ?? null}
                    devices={calls.softphone?.devices ?? []}
                    onMicDevice={(id) => calls.softphone?.setMicDevice(id)}
                    onMicOpened={() => void calls.softphone?.refreshDevices()}
                    pending={calls.busy}
                    replying={calls.replying}
                    awaitingReport={
                      !calls.finished &&
                      attempt.reports_expected &&
                      REPORTING_STATUSES.has(attempt.response_status) &&
                      shownCall.kind === "outgoing" &&
                      shownCall.service === ownService &&
                      // Nothing passed, nothing sent: no report to wait for (18.10.2026).
                      attempt.squad_state !== "waiting"
                    }
                    noVehicles={attempt.squad_state === "no_vehicles"}
                    headNumber={book.data?.entries.find((e) => e.role === "head")?.number ?? null}
                    error={calls.error ? calls.error.message : null}
                    onSay={(text, actionId) =>
                      calls.say(shownCall.id, text, actionId)
                    }
                    onSpeak={(blob, actionId) =>
                      calls.speak(shownCall.id, blob, actionId)
                    }
                    onAnswer={calls.answer}
                    cloud={calls.cloudLesson ? calls.cloudState : "off"}
                    cloudOnly={calls.cloudLesson && !shownCall.telephony}
                    muted={calls.cloudMuted}
                    onToggleMute={calls.toggleCloudMute}
                    onEnd={calls.hangUp}
                  />
                </div>
                {shownCall.ended_at !== null && (
                  <button
                    type="button"
                    onClick={() => calls.showCall(null)}
                    className="self-start rounded-sm border border-[var(--arm-dark)] px-3 py-1 text-xs hover:bg-[var(--arm-panel-2)]"
                  >
                    К набору номера
                  </button>
                )}
              </>
            ) : (
              <form
                onSubmit={submit}
                className="flex flex-col gap-2"
                aria-label="Набор номера"
              >
                <div className="flex items-center gap-1 rounded-sm border border-[var(--arm-dark)] bg-[var(--arm-field)] px-2">
                  <input
                    value={number}
                    onChange={(e) => type(e.target.value)}
                    inputMode="tel"
                    placeholder="Номер"
                    aria-label="Набираемый номер"
                    className="h-10 min-w-0 flex-1 bg-transparent font-mono text-lg tracking-wider outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => type(number.slice(0, -1))}
                    disabled={!number}
                    aria-label="Стереть цифру"
                    className="p-1 disabled:opacity-30"
                  >
                    <Delete className="size-5" aria-hidden />
                  </button>
                </div>
                {calls.dialError && (
                  <p
                    className="text-xs font-medium text-[var(--arm-red)]"
                    role="alert"
                  >
                    {calls.dialError.message}
                  </p>
                )}
                <div className="grid grid-cols-3 gap-1.5">
                  {KEYS.map((key) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => type(number + key)}
                      className="h-11 rounded-sm border border-[var(--arm-panel-2)] bg-white text-lg font-medium hover:bg-[var(--arm-panel-2)]"
                    >
                      {key}
                    </button>
                  ))}
                </div>
                <button
                  type="submit"
                  disabled={!number.trim() || !calls.canCall}
                  className="flex h-11 items-center justify-center gap-2 rounded-sm bg-[var(--arm-green)] font-medium text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Phone className="size-4" aria-hidden /> Вызов
                </button>
              </form>
            )}

            <div className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold uppercase text-[var(--arm-text-muted)]">
                Справочник
              </span>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Поиск: должность, имя, номер"
                aria-label="Поиск по справочнику"
                className="h-8 rounded-sm border border-[var(--arm-panel-2)] bg-white px-2 text-sm outline-none"
              />
              {book.isError && (
                <p className="text-xs text-[var(--arm-red)]" role="alert">
                  {book.error.message}
                </p>
              )}
              <ul className="flex flex-col divide-y divide-[var(--arm-panel-2)] rounded-sm bg-white text-sm">
                {entries.map((e) => (
                  <li key={`${e.role}-${e.number ?? "card"}`}>
                    {e.number ? (
                      <button
                        type="button"
                        onClick={() => {
                          calls.showCall(null);
                          type(e.number ?? "");
                        }}
                        disabled={Boolean(openCall)}
                        className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left hover:bg-[var(--arm-panel-2)] disabled:cursor-not-allowed"
                        title="Набрать этот номер"
                      >
                        <span className="min-w-0">
                          <span className="block truncate font-medium">
                            {e.role_title}
                          </span>
                          <span className="block truncate text-xs text-[var(--arm-text-muted)]">
                            {e.name}
                          </span>
                        </span>
                        <span className="font-mono">{e.number}</span>
                      </button>
                    ) : (
                      <div className="flex items-center justify-between gap-2 px-2 py-1.5 text-[var(--arm-text-muted)]">
                        <span className="font-medium">{e.role_title}</span>
                        <span className="text-xs">{e.name}</span>
                      </div>
                    )}
                  </li>
                ))}
                {book.isSuccess && entries.length === 0 && (
                  <li className="px-2 py-1.5 text-xs text-[var(--arm-text-muted)]">
                    Ничего не найдено.
                  </li>
                )}
              </ul>
            </div>

            {recent.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase text-[var(--arm-text-muted)]">
                  Последние вызовы
                </span>
                <ul className="flex flex-col divide-y divide-[var(--arm-panel-2)] rounded-sm bg-white text-xs">
                  {recent.map((c) => (
                    <li key={c.id}>
                      <button
                        type="button"
                        onClick={() => calls.showCall(c.id)}
                        className="flex w-full flex-col px-2 py-1.5 text-left hover:bg-[var(--arm-panel-2)]"
                      >
                        <span className="font-medium">
                          {c.role_title ?? callTitle(c)}
                        </span>
                        <span className="text-[var(--arm-text-muted)]">
                          {describeCall(c)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>
      )}
    </>
  );
}
