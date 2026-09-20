import { useQueryClient } from "@tanstack/react-query";
import { Bell, Link2, MessageSquare, Phone, Plus, Timer, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  callInfo,
  dialogKey,
  useCallControl,
  useDialog,
  useIncidentFlags,
  useSaveDraft,
  useSubmitCard,
  useTypeServices,
  type DialogTurnOut,
} from "@/api/intake";
import { useClassifierTree, useServices } from "@/api/teacher";
import { attemptKey, NetworkError, type AttemptOut } from "@/api/training";
import { ErrorState, LoadingState } from "@/components/states";
import { formatDateTime, useNow } from "@/emulator/time";
import { ArmButton, TrainerPanel } from "@/emulator/widgets";
import { useSessionEvents, type SessionEvent } from "@/emulator/ws";
import { AddressForm } from "@/intake/address-form";
import { CallBlock } from "@/intake/call-panel";
import { DialogPanel } from "@/intake/dialog-panel";
import { newId, useCardDraft, type Card } from "@/intake/draft";
import { SignButton, SurveyCard } from "@/intake/survey-card";
import { cn } from "@/lib/utils";
import { useSoftphone } from "@/softphone/context";

type CallStatus = "ringing" | "talking" | "ended";

const DESCRIPTION_MAX = 1999;
// The three big flag buttons of the screenshot; the rest come with the chosen type.
const PRIMARY_FLAGS: { code: string; title: string }[] = [
  { code: "injured", title: "Пострадавшие" },
  { code: "not_on_site", title: "Нет на месте / Отказ от скорой" },
  { code: "no_access", title: "Нет доступа / Заблокированные" },
];
const CALLER_ROLES = ["очевидец", "пострадавший", "родственник", "прохожий", "сотрудник", "жилец", "водитель", "иное"];

/** The operator-112 card with the call (PRD 13.5), for a call-intake attempt. */
export function CallCard({ attempt, connectionSeq }: { attempt: AttemptOut; connectionSeq?: number }) {
  const attemptId = attempt.id;
  const navigate = useNavigate();
  const client = useQueryClient();
  const now = useNow();
  const closed = attempt.state === "finished" || attempt.state === "evaluated";
  const intake = attempt.intake;

  const dialogQuery = useDialog(attemptId);
  const control = useCallControl(attemptId);
  const saveDraft = useSaveDraft(attemptId);
  const submit = useSubmitCard(attemptId);
  const tree = useClassifierTree();
  const services = useServices();
  const flags = useIncidentFlags();

  const [nextCallId, setNextCallId] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [addingService, setAddingService] = useState(false);
  const [hintsOn, setHintsOn] = useState(true);

  const { draft, update, setSubmissionId, clear } = useCardDraft(attemptId, intake?.draft ?? null, (card, updatedAt) => {
    if (!closed) saveDraft.mutate({ card, updated_at: updatedAt });
  });
  const card = draft.card;

  const onEvent = useCallback(
    (event: SessionEvent) => {
      const id = event.payload.attempt_id as string | undefined;
      if (id === attemptId) {
        void client.invalidateQueries({ queryKey: attemptKey(attemptId) });
        void client.invalidateQueries({ queryKey: dialogKey(attemptId) });
      } else if (event.type === "attempt.issued" && id) setNextCallId(id);
    },
    [attemptId, client],
  );
  const connection = useSessionEvents(attempt.session.id, connectionSeq ?? attempt.last_seq, onEvent);

  // --- the call ------------------------------------------------------------------------
  // The softphone (src/softphone/) owns the call: SIP through Asterisk or the browser
  // microphone. The server's DialogOut.call is the fallback (a reload, the teacher's view).
  const softphone = useSoftphone();
  const phone = softphone && softphone.attemptId === attempt.id ? softphone : null;
  const call = callInfo(dialogQuery.data, closed);
  const status: CallStatus = closed
    ? "ended"
    : phone?.status === "talking"
      ? "talking"
      : phone?.status === "ended" || call.state === "ended"
        ? "ended"
        : phone?.status === "incoming"
          ? "ringing"
          : call.state === "answered"
            ? "talking"
            : "ringing";
  const answeredAt = dialogQuery.data?.answered_at ?? null;
  const endedAt = call.ended_at ?? attempt.submitted_at ?? null;
  const talkSeconds = answeredAt ? Math.max(0, ((endedAt ? new Date(endedAt).getTime() : now) - new Date(answeredAt).getTime()) / 1000) : 0;

  // «нет контакта» / «срыв звонка»: through the softphone when it holds this call, else
  // straight to the API (the card opened without the softphone, e.g. after a reload).
  const mark = (action: "no-contact" | "call-dropped") => {
    if (phone) void (action === "no-contact" ? phone.noContact() : phone.callDropped());
    else control.mutate(action);
  };
  const callerSpoke = useCallback(
    (_turn: DialogTurnOut, callEnded: boolean) => {
      if (callEnded) void client.invalidateQueries({ queryKey: dialogKey(attemptId) });
    },
    [attemptId, client],
  );
  const marks = { no_contact: call.no_contact_marked, call_dropped: call.call_dropped_marked };

  // --- services: resolved from the type and the flags, plus the ones added by hand ------
  const activeFlags = useMemo(() => Object.keys(card.flags).filter((k) => card.flags[k]), [card.flags]);
  const typeServices = useTypeServices(card.incident_type, activeFlags);
  useEffect(() => {
    if (closed) return;
    // Without a type there are no automatic services; while the type's list is loading
    // (placeholder data belongs to the previous type) nothing changes.
    let auto: string[];
    if (!card.incident_type) auto = [];
    else if (typeServices.data?.type_code === card.incident_type) auto = typeServices.data.services.map((s) => s.code);
    else return;
    const wanted = [...auto, ...draft.manual_services.filter((s) => !auto.includes(s))];
    if (wanted.join(",") === card.services.join(",")) return;
    update((c) => ({ card: { ...c, services: wanted } }));
  }, [typeServices.data, draft.manual_services, card.services, card.incident_type, closed, update]);
  const serviceTitles = useMemo(() => new Map((services.data ?? []).map((s) => [s.code, s])), [services.data]);
  const extraFlags = useMemo(() => {
    const available = typeServices.data?.available_flags ?? [];
    const primary = new Set(PRIMARY_FLAGS.map((f) => f.code));
    return (flags.data ?? []).filter((f) => available.includes(f.code) && !primary.has(f.code));
  }, [flags.data, typeServices.data]);

  const setCard = (patch: Partial<Card>) => update((c) => ({ card: { ...c, ...patch } }));
  const toggleFlag = (code: string) => update((c) => ({ card: { ...c, flags: { ...c.flags, [code]: !c.flags[code] } } }));
  const addService = (code: string) => {
    setAddingService(false);
    if (!code || card.services.includes(code)) return;
    update((c, manual) => ({ card: { ...c, services: [...c.services, code] }, manual: [...manual, code] }));
  };

  // --- saving --------------------------------------------------------------------------
  const save = useCallback(() => {
    if (closed || submit.isPending) return;
    const id = draft.submission_id ?? newId();
    setSubmissionId(id);
    submit.mutate(
      { card, client_submission_id: id },
      {
        onSuccess: (data) => {
          clear();
          if (data.issued[0]) setNextCallId(data.issued[0]);
        },
      },
    );
  }, [card, clear, closed, draft.submission_id, setSubmissionId, submit]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const typing = target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (e.ctrlKey && e.key === "Enter") {
        e.preventDefault();
        save();
      } else if (e.key === "?" && !typing) {
        e.preventDefault();
        setShowHelp((v) => !v);
      } else if (e.key === "Escape") setShowHelp(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  const evaluation = attempt.evaluation as { total: number; passed: boolean } | null;
  const error = submit.error ?? control.error;
  const nextId = nextCallId;
  const dialog = dialogQuery.data;
  const norm = attempt.norm_seconds;
  const overNorm = talkSeconds > norm;

  return (
    // Fixed to the viewport like the real workplace: the conversation scrolls inside its
    // panel instead of growing the page (docs/BUGS.md, 11).
    <div className="arm flex h-dvh min-w-[1280px] overflow-hidden">
      <div className="arm-scroll flex min-h-0 min-w-0 flex-1 flex-col gap-1 overflow-y-auto p-1">
        {/* Top: the call panel, phones, incident number, the conversation timer. */}
        <header className="flex items-start gap-1 text-xs">
          <CallBlock phone={phone ?? softphone} />
          <PhoneBox label="АОН" value={intake?.caller_phone ?? ""} />
          <PhoneBox label="предоставленный" value={card.caller.phone} onChange={(v) => !closed && setCard({ caller: { ...card.caller, phone: v } })} />
          <PhoneBox label="телефон на месте" value="" />
          <div className="flex min-w-[9rem] flex-1 flex-col justify-center bg-[var(--arm-panel)] px-3 py-1 text-[11px] leading-tight">
            <span className="text-sm font-semibold">Происшествие {attempt.card.number}</span>
            <span className="text-[var(--arm-text-muted)]">
              {attempt.submitted_at ? `Сохр. ${formatDateTime(attempt.submitted_at)}` : "не сохранено"}
            </span>
            <span className="truncate text-[var(--arm-text-muted)]" title={attempt.arm.dispatcher}>
              Опер. {attempt.arm.operator_no}, АРМ {attempt.arm.arm_no}, {attempt.arm.dispatcher}
            </span>
          </div>
          <div
            className={cn(
              "flex w-28 shrink-0 flex-col items-center justify-center bg-[var(--arm-dark)] text-[var(--arm-on-dark)]",
              overNorm && !closed && "bg-[var(--arm-red)]",
            )}
            aria-label={`Длительность разговора ${Math.floor(talkSeconds)} секунд`}
            data-testid="talk-timer"
          >
            <span className="font-mono text-3xl font-semibold tabular-nums leading-none">{clock(talkSeconds)}</span>
            <span className="mt-1 flex gap-4 text-[9px] uppercase text-[var(--arm-on-dark-muted)]">
              <span>минут</span>
              <span>секунд</span>
            </span>
          </div>
        </header>

        {/* Middle: caller, address, description on the left; flags and the survey card on the right. */}
        <div className="grid min-h-0 flex-1 grid-cols-2 gap-1">
          <div className="flex min-h-0 flex-col gap-1">
            <div className="flex items-end gap-2 bg-[var(--arm-panel)] px-3 py-2">
              <input
                aria-label="Фамилия и имя заявителя"
                placeholder="Фамилия и имя заявителя"
                value={card.caller.name}
                disabled={closed}
                onChange={(e) => setCard({ caller: { ...card.caller, name: e.target.value } })}
                className="h-7 w-72 border-b border-[#a9adb2] bg-transparent text-sm focus:border-[var(--arm-blue)] focus:outline-none"
              />
              <select
                aria-label="Статус заявителя"
                value={card.caller.role}
                disabled={closed}
                onChange={(e) => setCard({ caller: { ...card.caller, role: e.target.value } })}
                className="h-7 w-44 border-b border-[#a9adb2] bg-transparent text-sm text-[var(--arm-text-muted)] focus:border-[var(--arm-blue)] focus:outline-none"
              >
                <option value="">выберите статус</option>
                {CALLER_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
            <AddressForm address={card.address} onChange={(address) => setCard({ address })} disabled={closed} />
            <div className="flex min-h-0 flex-1 flex-col bg-[var(--arm-panel)] px-3 py-2">
              <label htmlFor="description" className="text-[10px] text-[var(--arm-text-muted)]">
                Описание со слов заявителя
              </label>
              <textarea
                id="description"
                placeholder="введите"
                value={card.description}
                disabled={closed}
                maxLength={DESCRIPTION_MAX}
                onChange={(e) => setCard({ description: e.target.value })}
                className="min-h-24 flex-1 resize-none border-b border-[#a9adb2] bg-transparent text-sm leading-snug focus:border-[var(--arm-blue)] focus:outline-none"
              />
              <span className="self-end text-[10px] text-[var(--arm-text-muted)]" data-testid="description-counter">
                {card.description.length} / {DESCRIPTION_MAX}
              </span>
            </div>
          </div>

          <div className="flex min-h-0 flex-col gap-1">
            <div className="flex items-start gap-1 bg-[var(--arm-panel)] px-2 py-2">
              <div className="flex flex-wrap gap-1" role="group" aria-label="Признаки">
                {PRIMARY_FLAGS.map((f) => (
                  <FlagButton key={f.code} active={Boolean(card.flags[f.code])} disabled={closed} onClick={() => toggleFlag(f.code)}>
                    {f.title}
                  </FlagButton>
                ))}
                {extraFlags.map((f) => (
                  <FlagButton key={f.code} active={Boolean(card.flags[f.code])} disabled={closed} onClick={() => toggleFlag(f.code)} title={f.column_hint ?? undefined} small>
                    {f.title}
                  </FlagButton>
                ))}
              </div>
              <div className="ml-auto flex shrink-0 gap-1 border-l border-[#a9adb2] pl-2">
                <MarkButton active={marks.no_contact} disabled={closed || status === "ended"} onClick={() => mark("no-contact")} testId="mark-no-contact">
                  нет контакта
                </MarkButton>
                <MarkButton active={marks.call_dropped} disabled={closed || (status === "ended" && call.end_reason !== "caller_hangup")} onClick={() => mark("call-dropped")} testId="mark-call-dropped">
                  срыв звонка
                </MarkButton>
              </div>
            </div>
            {tree.isPending ? (
              <LoadingState text="Загружаем классификатор…" />
            ) : tree.isError ? (
              <ErrorState message={tree.error.message} onRetry={() => void tree.refetch()} />
            ) : (
              <SurveyCard
                groups={tree.data.groups}
                selection={{ signs_path: card.signs_path, incident_type: card.incident_type }}
                number={attempt.card.number}
                disabled={closed}
                onChange={(selection) => setCard(selection)}
              />
            )}
          </div>
        </div>

        {/* Bottom: the orange «Службы» strip. */}
        <div className="flex items-stretch bg-[var(--arm-orange)] text-white" data-testid="services-strip">
          <div className="flex w-[88px] shrink-0 items-center px-3 text-xs">Службы:</div>
          <div className="arm-scroll flex min-w-0 flex-1 overflow-x-auto">
          {card.services.map((code) => {
            const s = serviceTitles.get(code);
            return (
              <div key={code} className="flex w-[104px] shrink-0 flex-col justify-between border-r border-white/30 bg-[#7d838a] px-2 py-1" data-service={code} title={s?.title ?? code}>
                <span className="flex items-center justify-between text-[10px] text-white/80">
                  <Phone className="size-3" aria-hidden />
                  <X className="size-3 opacity-50" aria-hidden />
                </span>
                <span className="truncate text-center text-xs font-semibold">{s?.short_title ?? code}</span>
              </div>
            );
          })}
          </div>
          <div className="relative flex shrink-0 items-center px-2">
            <button
              type="button"
              aria-label="Добавить службу"
              disabled={closed}
              onClick={() => setAddingService((v) => !v)}
              className="flex size-8 items-center justify-center rounded-sm border border-white/70 hover:bg-white/10 disabled:opacity-50"
            >
              <Plus className="size-4" />
            </button>
            {addingService && (
              <select
                aria-label="Служба для добавления"
                autoFocus
                defaultValue=""
                onChange={(e) => addService(e.target.value)}
                onBlur={() => setAddingService(false)}
                className="absolute bottom-full left-2 z-10 mb-1 h-8 w-64 rounded-sm border border-[#a9adb2] bg-white px-2 text-sm text-[var(--arm-text)]"
              >
                <option value="">Выберите службу…</option>
                {(services.data ?? [])
                  .filter((s) => !card.services.includes(s.code))
                  .map((s) => (
                    <option key={s.code} value={s.code}>
                      {s.title}
                    </option>
                  ))}
              </select>
            )}
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-1 px-2">
            <button
              type="button"
              onClick={save}
              disabled={closed || submit.isPending}
              title="Сохранить (Ctrl+Enter)"
              className="h-9 rounded-sm bg-white px-6 text-base font-semibold text-[var(--arm-orange)] hover:bg-[#fff1ea] disabled:opacity-60"
              data-testid="save-card"
            >
              {submit.isPending ? "сохраняем…" : "сохранить"}
            </button>
            {[Link2, Timer, Bell, MessageSquare].map((Icon, i) => (
              <span key={i} className="flex size-8 items-center justify-center rounded-sm border border-white/60" aria-hidden>
                <Icon className="size-4" />
              </span>
            ))}
            <Link
              to={`/student/sessions/${attempt.session.id}/calls`}
              aria-label="Закрыть карточку и вернуться к вызовам"
              className="flex size-8 items-center justify-center rounded-sm border border-white/60 hover:bg-white/10"
            >
              <X className="size-4" />
            </Link>
          </div>
        </div>
        {error && (
          <p className="px-2 text-xs text-[var(--arm-red)]" role="alert">
            {error instanceof NetworkError ? `${error.message} Нажмите «сохранить» ещё раз: карточка не задвоится.` : error.message}
          </p>
        )}
      </div>

      <TrainerPanel
        connection={connection}
        width="wide"
        footer={
          <button type="button" onClick={() => setShowHelp(true)} className="text-left text-xs text-[var(--arm-blue-dark)] underline-offset-2 hover:underline">
            Горячие клавиши (?)
          </button>
        }
      >
        <div>
          <div className="text-xs text-[var(--arm-text-muted)]">Вызов {attempt.card.number}</div>
          <div className="font-medium leading-tight">{attempt.session.title}</div>
        </div>
        <div className="flex items-baseline justify-between rounded-sm bg-[var(--arm-field)] p-2 text-xs">
          <span className="text-[var(--arm-text-muted)]">Норматив {norm} с на приём вызова</span>
          <span className={cn("font-mono tabular-nums", overNorm ? "text-[var(--arm-red)]" : "text-[var(--arm-text)]")} data-testid="norm-timer">
            {answeredAt ? clock(talkSeconds) : "—"}
          </span>
        </div>
        {dialogQuery.isPending ? (
          <LoadingState text="Соединяем…" />
        ) : dialogQuery.isError ? (
          <ErrorState message={dialogQuery.error.message} onRetry={() => void dialogQuery.refetch()} />
        ) : dialog ? (
          <DialogPanel
            attemptId={attemptId}
            dialog={dialog}
            active={status === "talking"}
            hints={attempt.session.hints_enabled && hintsOn}
            telephony={call.telephony}
            input={!phone}
            onCallerSpoke={callerSpoke}
          />
        ) : null}
        {attempt.session.hints_enabled && (
          <label className="inline-flex items-center gap-2 text-xs text-[var(--arm-text-muted)]">
            <input type="checkbox" checked={hintsOn} onChange={(e) => setHintsOn(e.target.checked)} /> подсказки
          </label>
        )}
        {closed && (
          <div
            className={cn(
              "flex flex-col gap-2 rounded-sm border p-2 text-xs",
              evaluation?.passed === false ? "border-[var(--arm-red)] bg-[#fdeeed]" : "border-[var(--arm-green)] bg-[#eef8f0]",
            )}
            role="status"
          >
            <b>Карточка сохранена</b>
            {evaluation ? (
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-semibold tabular-nums" data-testid="card-score">
                  {evaluation.total}
                </span>
                <span className={evaluation.passed ? "font-semibold text-[var(--arm-green)]" : "font-semibold text-[var(--arm-red)]"}>
                  {evaluation.passed ? "Зачтено" : "Не зачтено"}
                </span>
              </div>
            ) : (
              <span className="text-[var(--arm-text-muted)]">Оценка считается…</span>
            )}
            <Link to={`/student/attempts/${attempt.id}/review`} className="text-[var(--arm-blue-dark)] underline-offset-2 hover:underline">
              Открыть разбор
            </Link>
            {nextId ? (
              <ArmButton variant="blue" className="normal-case" onClick={() => navigate(`/student/attempts/${nextId}`)}>
                Следующий вызов
              </ArmButton>
            ) : (
              <ArmButton variant="blue" className="normal-case" onClick={() => navigate(`/student/sessions/${attempt.session.id}/calls`)}>
                Следующий вызов
              </ArmButton>
            )}
          </div>
        )}
      </TrainerPanel>

      {showHelp && <HotkeysHelp onClose={() => setShowHelp(false)} />}
    </div>
  );
}

function clock(seconds: number): string {
  const total = Math.floor(seconds);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function PhoneBox({ label, value, onChange }: { label: string; value: string; onChange?: (value: string) => void }) {
  return (
    <div className="flex items-center gap-1.5 bg-[var(--arm-panel)] px-2 py-1">
      <Phone className="size-4 text-[var(--arm-text-muted)]" aria-hidden />
      <div className="flex w-[5rem] flex-col">
        <span className="text-[9px] text-[var(--arm-text-muted)]">{label}</span>
        {onChange ? (
          <input
            aria-label={label}
            value={value}
            placeholder="+7 ( ) - -"
            onChange={(e) => onChange(e.target.value)}
            className="h-5 border-b border-[#a9adb2] bg-transparent text-sm tabular-nums focus:border-[var(--arm-blue)] focus:outline-none"
          />
        ) : (
          <span className="border-b border-[#a9adb2] text-sm tabular-nums" data-testid={label === "АОН" ? "aon" : undefined}>
            {value || "+7 ( ) - -"}
          </span>
        )}
      </div>
    </div>
  );
}

function FlagButton({ active, small, className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { active: boolean; small?: boolean }) {
  return (
    <SignButton
      active={active}
      className={cn(small ? "min-h-6 text-[11px]" : "min-h-10 px-3 text-xs font-semibold", className)}
      {...props}
    />
  );
}

function MarkButton({ active, testId, className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { active: boolean; testId: string }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      data-testid={testId}
      className={cn(
        "inline-flex min-h-10 items-center rounded-sm border px-3 text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        active ? "border-[var(--arm-orange)] bg-[var(--arm-orange)] text-white" : "border-[var(--arm-orange)] bg-white text-[var(--arm-orange)] hover:bg-[#fff1ea]",
        className,
      )}
      {...props}
    />
  );
}

function HotkeysHelp({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40" role="dialog" aria-modal="true" aria-label="Горячие клавиши" onClick={onClose}>
      <div className="w-96 rounded-sm bg-white p-4 text-sm shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <b>Горячие клавиши</b>
          <button type="button" aria-label="Закрыть" onClick={onClose}>
            <X className="size-4" />
          </button>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
          <dt><kbd className="rounded border px-1 font-mono text-xs">Ctrl+Enter</kbd></dt><dd>сохранить карточку</dd>
          <dt><kbd className="rounded border px-1 font-mono text-xs">Enter</kbd></dt><dd>в поле разговора — сказать заявителю</dd>
          <dt><kbd className="rounded border px-1 font-mono text-xs">Tab</kbd></dt><dd>переход по полям: заявитель → адрес → описание</dd>
          <dt><kbd className="rounded border px-1 font-mono text-xs">Esc</kbd></dt><dd>закрыть подсказку</dd>
          <dt><kbd className="rounded border px-1 font-mono text-xs">?</kbd></dt><dd>эта подсказка</dd>
        </dl>
      </div>
    </div>
  );
}
