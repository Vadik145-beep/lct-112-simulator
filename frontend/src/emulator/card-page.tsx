import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  MapPin,
  MessageSquare,
  MessageSquareWarning,
  Pencil,
  Phone,
  PhoneOff,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import {
  attemptKey,
  NetworkError,
  useAttempt,
  useAnswerServiceCall,
  useEndServiceCall,
  useFinishAttempt,
  useFlagField,
  useOpenAttempt,
  useProgressReporter,
  useSayToOfficer,
  useSetStatus,
  useSpeakToOfficer,
  useStartServiceCall,
  useUnflagField,
  type AttemptOut,
  type TransitionOut,
} from "@/api/training";
import { ErrorState, LoadingState } from "@/components/states";
import { newActionId, useDraft } from "@/emulator/draft";
import { FlagButton, FlagEditor, FlagMark } from "@/emulator/flag-field";
import {
  ADDRESS_PARTS,
  FIELD_TITLES,
  type FlagRequest,
} from "@/emulator/flag-field-model";
import { ServiceCallPanel } from "@/emulator/service-call";
import { describeCall } from "@/emulator/service-call-model";
import {
  acceptanceTimer,
  formatDate,
  formatDateTime,
  formatSeconds,
  formatTime,
  useNow,
} from "@/emulator/time";
import { ArmButton, TimerBadge, TrainerPanel } from "@/emulator/widgets";
import { useSessionEvents, type SessionEvent } from "@/emulator/ws";
import { cn } from "@/lib/utils";
import { useSoftphone } from "@/softphone/context";

const STATUS_ACCEPTED = "accepted";
const STATUS_REJECTED = "rejected";

// Training hints per current status (memo, pages 21-26). Shown only when the session allows.
const HINTS: Record<string, string> = {
  added:
    "Карточка направлена в вашу службу. Откройте её: система поставит «Получена службой».",
  received:
    "Поставьте «Принята» или «Не принята» в течение норматива. «Не принята» только с комментарием: причина и кому передана информация.",
  accepted:
    "Реагирование будет. Дальше по ходу работ: «Начало реагирования» с номером наряда, «Прибытие», «Проведение работ», «Работы завершены» с результатом.",
  rejected:
    "Если ситуация изменилась и служба всё же реагирует, доступна только «Принята».",
  response_started:
    "Силы направлены. Отметьте «Прибытие» по факту прибытия на место.",
  arrived: "Отметьте «Проведение работ», когда работы начаты.",
  works_started:
    "По окончании — «Работы завершены» с комментарием о результатах: статус закрывает карточку.",
};
// The same hints when the squad reports by phone (customer, 21.09.2026): the statuses of
// the response follow the reports of the squad leader, not the dispatcher's guess.
const REPORT_HINTS: Record<string, string> = {
  accepted:
    "Реагирование будет. Позвоните дежурному службы и передайте карточку. Старший группы реагирования будет звонить с докладами — о выезде, прибытии, работах и их завершении; ответьте на звонок и отражайте каждый доклад статусом с комментарием, а не наперёд.",
  response_started:
    "Бригада в пути. Дождитесь доклада «на месте» и поставьте «Прибытие»; можно позвонить дежурному и уточнить ход работ.",
  arrived:
    "Бригада на месте. По докладу о начале работ — «Проведение работ» с тем, что делают.",
  works_started:
    "Работы идут. По докладу о завершении — «Работы завершены» с результатом из доклада: статус закрывает карточку.",
};

function hintFor(attempt: AttemptOut): string {
  const table = attempt.reports_expected ? REPORT_HINTS : HINTS;
  return (
    table[attempt.response_status] ??
    HINTS[attempt.response_status] ??
    "Действуйте по памятке."
  );
}

export function CardPage() {
  const { attemptId } = useParams<{ attemptId: string }>();
  if (!attemptId) return <Navigate to="/student" replace />;
  return <Card attemptId={attemptId} />;
}

function Card({ attemptId }: { attemptId: string }) {
  const query = useAttempt(attemptId);
  const open = useOpenAttempt(attemptId);
  const client = useQueryClient();
  const [nextCardId, setNextCardId] = useState<string | null>(null);

  // Opening the card is what puts «Получена службой» on it (memo, page 21).
  const opened = useRef(false);
  useEffect(() => {
    if (query.data?.state === "issued" && !opened.current) {
      opened.current = true;
      open.mutate();
    }
  }, [query.data?.state, open]);

  const onEvent = useCallback(
    (event: SessionEvent) => {
      const id = event.payload.attempt_id as string | undefined;
      if (id === attemptId)
        void client.invalidateQueries({ queryKey: attemptKey(attemptId) });
      else if (event.type === "attempt.issued" && id) setNextCardId(id);
    },
    [attemptId, client],
  );
  const connection = useSessionEvents(
    query.data?.session.id,
    query.data?.last_seq,
    onEvent,
  );

  if (query.isPending) return <LoadingState text="Открываем карточку…" />;
  if (query.isError || !query.data) {
    return (
      <div className="p-6">
        <ErrorState
          message={query.error?.message ?? "Не удалось открыть карточку."}
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }
  return (
    <CardView
      attempt={query.data}
      connection={connection}
      nextCardId={nextCardId}
    />
  );
}

function CardView({
  attempt,
  connection,
  nextCardId,
}: {
  attempt: AttemptOut;
  connection: ReturnType<typeof useSessionEvents>;
  nextCardId: string | null;
}) {
  const navigate = useNavigate();
  const now = useNow();
  const setStatus = useSetStatus(attempt.id);
  const finish = useFinishAttempt(attempt.id);
  const { draft, setDraft, clearDraft } = useDraft(attempt.id);
  const reportProgress = useProgressReporter(attempt.id);
  const [panelOpen, setPanelOpen] = useState(true);
  const [showHelp, setShowHelp] = useState(false);
  const [hintsOn, setHintsOn] = useState(true);
  const [issuedNext, setIssuedNext] = useState<string | null>(null);
  const [confirmFinish, setConfirmFinish] = useState(false);
  const editorRef = useRef<HTMLDivElement>(null);
  // «Отметить ошибку» (issue #35): which field's editor is open («address» covers its parts).
  const [flagTarget, setFlagTarget] = useState<string | null>(null);
  const flagField = useFlagField(attempt.id);
  const unflagField = useUnflagField(attempt.id);

  const card = attempt.card;
  const finished =
    attempt.state === "finished" || attempt.state === "evaluated";
  // Calls to service officers (issue #36): one open at a time; the softphone carries the
  // voice when telephony is on, the panel below the card shows the conversation either way.
  const softphone = useSoftphone();
  const startCall = useStartServiceCall(attempt.id);
  const sayToOfficer = useSayToOfficer(attempt.id);
  const speakToOfficer = useSpeakToOfficer(attempt.id);
  const endCall = useEndServiceCall(attempt.id);
  const answerCall = useAnswerServiceCall(attempt.id);
  // Whether the stt service answers comes with every service-call response.
  const [sttAvailable, setSttAvailable] = useState(false);
  const openCall =
    attempt.service_calls.find((c) => c.ended_at === null) ?? null;
  const [shownCallId, setShownCallId] = useState<string | null>(null);
  const shownCall =
    openCall ?? attempt.service_calls.find((c) => c.id === shownCallId) ?? null;
  const callBusy =
    startCall.isPending ||
    sayToOfficer.isPending ||
    speakToOfficer.isPending ||
    answerCall.isPending ||
    endCall.isPending;
  const callError =
    startCall.error ??
    sayToOfficer.error ??
    speakToOfficer.error ??
    answerCall.error ??
    endCall.error;
  const callsByService = useMemo(() => {
    const map = new Map<string, AttemptOut["service_calls"]>();
    for (const c of attempt.service_calls)
      map.set(c.service, [...(map.get(c.service) ?? []), c]);
    return map;
  }, [attempt.service_calls]);
  const dial = (service: string) => {
    if (finished || openCall || callBusy) return;
    startCall.mutate(service, {
      onSuccess: (data) => {
        setShownCallId(data.call.id);
        setSttAvailable(data.stt_available);
      },
    });
  };
  // The squad's report is an incoming call (issue #103): the trainee answers it. The phone
  // carries the voice when it is the one ringing; otherwise the card answers over the API.
  const answerReport = () => {
    if (!openCall || callBusy) return;
    if (softphone?.serviceCallId === openCall.id) {
      void softphone.answer();
      return;
    }
    answerCall.mutate(openCall.id, {
      onSuccess: (data) => setSttAvailable(data.stt_available),
    });
  };
  const hangUpService = () => {
    if (!openCall) return;
    endCall.mutate(openCall.id, {
      onSuccess: () => {
        // The SIP leg of the officer's call goes down with the record.
        if (softphone?.serviceCallId) void softphone.hangup();
      },
    });
  };
  const flagsByField = useMemo(
    () => new Map(attempt.flagged_fields.map((f) => [f.field, f])),
    [attempt.flagged_fields],
  );
  const addressFlags = attempt.flagged_fields.filter((f) =>
    f.field.startsWith("address."),
  );
  const addressParts = ADDRESS_PARTS.filter((k) => card.address[k]).map(
    (k) => ({
      key: k,
      title: FIELD_TITLES[`address.${k}`] ?? k,
      value: card.address[k],
    }),
  );
  const flagError = flagField.error ?? unflagField.error;

  const submitFlag = useCallback(
    (request: FlagRequest) => {
      flagField.mutate(
        { ...request, action_id: newActionId() },
        { onSuccess: () => setFlagTarget(null) },
      );
    },
    [flagField],
  );
  const removeFlag = useCallback(
    (field: string) => unflagField.mutate(field),
    [unflagField],
  );
  const flagEditor = (target: string) =>
    flagTarget === target && !finished ? (
      <FlagEditor
        field={target}
        current={
          target === "address" ? addressFlags[0] : flagsByField.get(target)
        }
        services={card.services}
        addressParts={addressParts}
        pending={flagField.isPending}
        error={flagError ? flagError.message : null}
        onSubmit={submitFlag}
        onCancel={() => {
          flagField.reset();
          setFlagTarget(null);
        }}
      />
    ) : null;
  const toggleFlag = (target: string) =>
    setFlagTarget((v) => (v === target ? null : target));

  // The teacher's monitoring shows whether the trainee is reading or filling the status row.
  useEffect(() => {
    if (!finished) reportProgress(draft.open ? "editing_status" : "viewing");
  }, [draft.open, finished, reportProgress]);
  const evaluation = attempt.evaluation as {
    total: number;
    passed: boolean;
  } | null;
  const transitions = attempt.transitions;
  const byCode = useMemo(
    () => new Map(transitions.map((t) => [t.code, t])),
    [transitions],
  );
  const selected: TransitionOut | undefined =
    byCode.get(draft.status) ?? transitions[0];
  const timer = acceptanceTimer(
    attempt.issued_at,
    attempt.primary_status_at,
    attempt.norm_seconds,
    now,
  );
  const error = setStatus.error ?? finish.error;
  const nextId = issuedNext ?? nextCardId;
  // The live АРМ-112 keeps the order number once entered (screenshots of 17.09.2026: «23» stays
  // in the row from «Принята» to «Работы завершены»). The row opens with the last one, editable.
  const lastOrderNumber = useMemo(
    () =>
      [...attempt.status_log].reverse().find((e) => e.order_number)
        ?.order_number ?? "",
    [attempt.status_log],
  );

  const openEditor = useCallback(
    (status?: string) => {
      if (finished || transitions.length === 0) return;
      setDraft((prev) => ({
        ...prev,
        open: true,
        status:
          status && byCode.has(status)
            ? status
            : byCode.has(prev.status)
              ? prev.status
              : (transitions[0]?.code ?? ""),
        order_number: prev.order_number || lastOrderNumber,
      }));
      setPanelOpen(true);
      setTimeout(
        () =>
          editorRef.current
            ?.querySelector<HTMLElement>("select, input, textarea")
            ?.focus(),
        0,
      );
    },
    [byCode, finished, lastOrderNumber, setDraft, transitions],
  );

  const submit = useCallback(() => {
    if (!selected || setStatus.isPending) return;
    const comment = draft.comment.trim();
    if (selected.requires_comment && !comment) {
      setStatus.reset();
      setDraft({ open: true });
      return;
    }
    const actionId = draft.action_id ?? newActionId();
    setDraft({ action_id: actionId });
    setStatus.mutate(
      {
        status: selected.code,
        order_number: draft.order_number.trim() || null,
        comment: comment || null,
        reject_reason:
          selected.code === STATUS_REJECTED && draft.reject_reason
            ? draft.reject_reason
            : null,
        action_id: actionId,
      },
      {
        onSuccess: (data) => {
          clearDraft();
          if (data.issued[0]) setIssuedNext(data.issued[0]);
        },
      },
    );
  }, [clearDraft, draft, selected, setDraft, setStatus]);

  const cancelEditor = useCallback(() => {
    setStatus.reset();
    clearDraft();
  }, [clearDraft, setStatus]);

  // Hotkeys (PRD 13.5): Alt+A «Принята», Alt+R «Не принята», Ctrl+Enter save, ? help.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const typing =
        target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (e.altKey && (e.code === "KeyA" || e.code === "KeyR")) {
        e.preventDefault();
        openEditor(e.code === "KeyA" ? STATUS_ACCEPTED : STATUS_REJECTED);
      } else if (e.ctrlKey && e.key === "Enter") {
        e.preventDefault();
        if (draft.open) submit();
      } else if (e.key === "Escape" && draft.open) {
        cancelEditor();
      } else if (e.key === "?" && !typing) {
        e.preventDefault();
        setShowHelp((v) => !v);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cancelEditor, draft.open, openEditor, submit]);

  const ownService = card.services.find((s) => s.is_own);
  const otherServices = card.services.filter((s) => !s.is_own);
  const lastEntry = attempt.status_log[attempt.status_log.length - 1];

  return (
    <div className="arm flex min-h-dvh min-w-[1280px]">
      <div className="flex min-w-0 flex-1 flex-col gap-1 p-1">
        {/* Top: call panel, phones, incident number, view buttons (screenshot page 23). */}
        <header className="flex gap-1 text-xs">
          <div className="flex items-center gap-2 bg-[var(--arm-panel)] px-2 py-1">
            <PhoneOff
              className="size-5 text-[var(--arm-text-muted)]"
              aria-hidden
            />
            <div className="flex flex-col gap-1">
              <span className="text-sm text-[var(--arm-text-muted)]">
                не подключен
              </span>
              <div className="flex gap-1">
                <ArmButton
                  className="h-5 px-1.5 text-[9px] normal-case"
                  disabled
                >
                  записи звонков
                </ArmButton>
                <ArmButton
                  className="h-5 px-1.5 text-[9px] normal-case"
                  disabled
                >
                  список SMS
                </ArmButton>
              </div>
            </div>
          </div>
          <PhoneBox label="АОН" value={card.phones.aon ?? ""} />
          <PhoneBox
            label="предоставленный"
            value={card.phones.provided ?? ""}
          />
          <PhoneBox
            label="телефон на месте"
            value={card.phones.on_site ?? ""}
          />
          <div className="flex min-w-0 flex-1 flex-col justify-center bg-[var(--arm-panel)] px-3 py-1 text-[11px] leading-tight">
            <span className="text-sm font-semibold">
              Происшествие {card.number}
            </span>
            <span className="text-[var(--arm-text-muted)]">
              Сохр. {formatDateTime(card.created_at)}
            </span>
            <span
              className="truncate text-[var(--arm-text-muted)]"
              title={attempt.arm.dispatcher}
            >
              Опер. {card.operator_no}, АРМ {card.arm_no},{" "}
              {attempt.arm.dispatcher}
            </span>
          </div>
          <div className="flex shrink-0 flex-col gap-1">
            <ArmButton variant="blue" className="h-6 px-2 normal-case">
              просмотр
            </ArmButton>
            <ArmButton variant="dark" className="h-6 px-2 normal-case" disabled>
              дополнение
            </ArmButton>
          </div>
        </header>

        {/* Middle: caller, address, description on the left; flags and classification on the right. */}
        <div className="grid flex-1 grid-cols-2 gap-1">
          <div className="flex flex-col gap-1">
            <div className="flex items-baseline gap-2 bg-[var(--arm-panel)] px-3 py-2">
              <span className="text-base font-semibold">
                {card.caller.name || "Заявитель не указан"}
              </span>
              <span className="text-xs text-[var(--arm-text-muted)]">
                {card.caller.role}
              </span>
            </div>
            <div
              className={cn(
                "flex flex-col gap-1 bg-[var(--arm-panel)] px-3 py-2 text-sm font-medium",
                addressFlags.length > 0 &&
                  "outline outline-1 outline-[var(--arm-orange)]",
              )}
              data-testid="card-address"
            >
              <div className="flex items-center gap-2">
                <span className="flex-1">
                  {card.address.text || "Адрес не указан"}
                </span>
                <MapPin
                  className="size-4 text-[var(--arm-text-muted)]"
                  aria-hidden
                />
                <FlagButton
                  field="address.house"
                  flagged={addressFlags.length > 0}
                  disabled={finished || addressParts.length === 0}
                  onClick={() => toggleFlag("address")}
                />
              </div>
              {addressFlags.map((f) => (
                <FlagMark
                  key={f.field}
                  flag={f}
                  services={card.services}
                  disabled={finished}
                  onRemove={() => removeFlag(f.field)}
                />
              ))}
              {flagEditor("address")}
            </div>
            <div
              className={cn(
                "flex flex-1 flex-col gap-1 bg-[var(--arm-panel)] px-3 py-2 text-sm",
                flagsByField.has("description") &&
                  "outline outline-1 outline-[var(--arm-orange)]",
              )}
            >
              <div className="flex items-start gap-2">
                <span
                  className="flex-1"
                  aria-label="Описание со слов заявителя"
                >
                  {card.description}
                </span>
                <FlagButton
                  field="description"
                  flagged={flagsByField.has("description")}
                  disabled={finished}
                  onClick={() => toggleFlag("description")}
                />
              </div>
              {flagsByField.has("description") && (
                <FlagMark
                  flag={flagsByField.get("description")!}
                  services={card.services}
                  disabled={finished}
                  onRemove={() => removeFlag("description")}
                />
              )}
              {flagEditor("description")}
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <div
              className={cn(
                "flex flex-wrap items-center gap-x-4 gap-y-1 bg-[var(--arm-panel)] px-3 py-2 text-xs",
                flagsByField.has("flags.injured") &&
                  "outline outline-1 outline-[var(--arm-orange)]",
              )}
            >
              <span className="inline-flex items-center gap-1">
                Пострадавшие:{" "}
                <b>{card.injured ? (card.injured_count ? `да, ${card.injured_count}` : "да") : "нет"}</b>
                <FlagButton
                  field="flags.injured"
                  flagged={flagsByField.has("flags.injured")}
                  disabled={finished}
                  onClick={() => toggleFlag("flags.injured")}
                />
              </span>
              <span>
                Отказ от скорой: <b>{card.ambulance_refused ? "да" : "нет"}</b>
              </span>
              <span>
                Заблокированные: <b>{card.blocked ? "да" : "нет"}</b>
              </span>
              <span className="ml-auto flex items-center gap-1">
                <FlagBox active={card.emergency} label="ЧС">
                  <Zap className="size-3" aria-hidden />
                </FlagBox>
                <FlagBox active={card.incident_flag} label="ЧП">
                  <AlertTriangle className="size-3" aria-hidden />
                </FlagBox>
                <span
                  className="ml-1 flex size-7 items-center justify-center rounded-full border border-[#a9adb2]"
                  aria-hidden
                >
                  <Pencil className="size-3.5" />
                </span>
              </span>
              {flagsByField.has("flags.injured") && (
                <div className="w-full">
                  <FlagMark
                    flag={flagsByField.get("flags.injured")!}
                    services={card.services}
                    disabled={finished}
                    onRemove={() => removeFlag("flags.injured")}
                  />
                </div>
              )}
              <div className="w-full empty:hidden">
                {flagEditor("flags.injured")}
              </div>
            </div>
            <div className="bg-[var(--arm-dark)] px-3 py-2 text-sm font-semibold text-[var(--arm-on-dark)] underline decoration-[var(--arm-on-dark-muted)] underline-offset-4">
              {card.incident.group_title || "Группа не определена"}
            </div>
            <div className="bg-[var(--arm-panel)] px-3 py-2 text-sm font-semibold">
              {card.incident.signs.length > 0
                ? card.incident.signs.join(". ") + "."
                : "—"}
            </div>
            <div
              className={cn(
                "flex flex-col gap-1 bg-[var(--arm-panel)] px-3 py-2 text-sm",
                flagsByField.has("incident_type") &&
                  "outline outline-1 outline-[var(--arm-orange)]",
              )}
            >
              <div className="flex items-center gap-2">
                <span className="flex-1">
                  <span className="text-[var(--arm-text-muted)]">Класс.: </span>
                  <b>
                    {card.incident.final_title}
                    {card.incident.final_title ? ";" : ""}
                  </b>
                </span>
                <FlagButton
                  field="incident_type"
                  flagged={flagsByField.has("incident_type")}
                  disabled={finished}
                  onClick={() => toggleFlag("incident_type")}
                />
              </div>
              {flagsByField.has("incident_type") && (
                <FlagMark
                  flag={flagsByField.get("incident_type")!}
                  services={card.services}
                  disabled={finished}
                  onRemove={() => removeFlag("incident_type")}
                />
              )}
              {flagEditor("incident_type")}
            </div>
            <div className="bg-[var(--arm-panel)] px-3 py-2 text-sm text-[var(--arm-text-muted)]">
              [ВИС] Класс.:
            </div>
            <div className="flex-1" />
          </div>
        </div>

        {/* Bottom: the «Службы» strip with the expanded tab of our service and the status editor. */}
        <div className="relative mt-auto">
          {(flagsByField.has("services") || flagTarget === "services") && (
            <div
              className="ml-[104px] flex w-[900px] flex-col gap-1 bg-white p-1 shadow-lg"
              data-testid="services-flag"
            >
              {flagsByField.has("services") && (
                <FlagMark
                  flag={flagsByField.get("services")!}
                  services={card.services}
                  disabled={finished}
                  onRemove={() => removeFlag("services")}
                />
              )}
              {flagEditor("services")}
            </div>
          )}
          {panelOpen && ownService && (
            <div
              className="ml-[104px] w-[460px] bg-[var(--arm-blue)] text-white shadow-lg"
              data-testid="own-service-panel"
            >
              <div className="flex items-center justify-between px-3 py-2">
                <span className="text-sm font-medium">{ownService.title}</span>
                <button
                  type="button"
                  aria-label="Свернуть службу"
                  onClick={() => setPanelOpen(false)}
                  className="rounded-sm p-0.5 hover:bg-[var(--arm-blue-dark)]"
                >
                  <X className="size-4" />
                </button>
              </div>
              <ol
                className="flex flex-col gap-1 px-3 pb-3 text-xs"
                aria-label="История статусов"
              >
                {attempt.status_log.map((entry, i) => (
                  <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-white/80">
                      оп.{" "}
                      {entry.by === "system" ? "0" : attempt.arm.operator_no}
                    </span>
                    <span aria-hidden>›</span>
                    <span className="font-mono">
                      {formatDate(entry.at, true)} {formatTime(entry.at)}
                    </span>
                    <span className="font-medium">{entry.title}</span>
                    {entry.reject_reason_title && (
                      <span>: {entry.reject_reason_title}</span>
                    )}
                    {entry.order_number && (
                      <span className="text-white/80">
                        наряд {entry.order_number}
                      </span>
                    )}
                    {entry.comment && (
                      <>
                        <span aria-hidden>›</span>
                        <span className="text-white/90">{entry.comment}</span>
                      </>
                    )}
                  </li>
                ))}
                {attempt.service_calls.map((c) => (
                  <li
                    key={c.id}
                    className="flex flex-wrap items-baseline gap-x-2"
                    data-testid="service-call-line"
                  >
                    <span className="text-white/80">{c.service_title}</span>
                    <span aria-hidden>›</span>
                    <span className="font-mono">
                      {formatTime(c.started_at)}
                    </span>
                    <button
                      type="button"
                      className="text-left font-medium underline-offset-2 hover:underline"
                      onClick={() => setShownCallId(c.id)}
                    >
                      {describeCall(c)}
                    </button>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {draft.open && !finished && selected && (
            <div
              ref={editorRef}
              className="ml-[104px] flex w-[900px] items-start gap-1 bg-white p-1 shadow-lg"
              role="form"
              aria-label="Проставление статуса"
            >
              <div className="flex flex-1 flex-col gap-1">
                <div className="flex gap-1">
                  <select
                    aria-label="Статус"
                    value={selected.code}
                    onChange={(e) => setDraft({ status: e.target.value })}
                    className="h-8 w-56 border-b border-[#a9adb2] bg-white px-2 text-sm focus:border-[var(--arm-blue)] focus:outline-none"
                  >
                    {transitions.map((t) => (
                      <option key={t.code} value={t.code}>
                        {t.title}
                      </option>
                    ))}
                  </select>
                  {/* The live row is always «Статус | Номер наряда | Комментарий»: the field stays
                      even where the number is optional (docs/DECISIONS.md, карточка ДДС). */}
                  <input
                    aria-label="Номер наряда"
                    placeholder={
                      selected.requires_order_number
                        ? "Номер наряда (обязателен)"
                        : "Номер наряда"
                    }
                    value={draft.order_number}
                    onChange={(e) =>
                      setDraft({ order_number: e.target.value })
                    }
                    className={cn(
                      "h-8 w-44 border-b px-2 text-sm placeholder:text-[var(--arm-text-muted)] focus:border-[var(--arm-blue)] focus:outline-none",
                      selected.requires_order_number &&
                        !draft.order_number.trim()
                        ? "border-[var(--arm-orange)]"
                        : "border-[#a9adb2]",
                    )}
                  />
                  <input
                    aria-label="Комментарий"
                    placeholder={
                      selected.code === STATUS_REJECTED
                        ? "Причина отказа и кому передана информация"
                        : selected.requires_comment
                          ? "Комментарий обязателен"
                          : "Комментарий"
                    }
                    value={draft.comment}
                    onChange={(e) => setDraft({ comment: e.target.value })}
                    required={selected.requires_comment}
                    className={cn(
                      "h-8 flex-1 border-b px-2 text-sm placeholder:text-[var(--arm-text-muted)] focus:border-[var(--arm-blue)] focus:outline-none",
                      selected.requires_comment && !draft.comment.trim()
                        ? "border-[var(--arm-orange)]"
                        : "border-[#a9adb2]",
                    )}
                  />
                </div>
                {!setStatus.isPaused &&
                  !error &&
                  !selected.requires_comment &&
                  !selected.requires_order_number && (
                    <p className="px-1 text-xs text-[var(--arm-text-muted)]">
                      Для «{selected.title}» заполнять ничего не нужно: нажмите
                      ✓ (Ctrl+Enter).
                    </p>
                  )}
                {setStatus.isPaused && (
                  <p
                    className="px-1 text-xs text-[var(--arm-orange)]"
                    role="alert"
                  >
                    Нет связи с сервером. Статус отправится сам, как только
                    связь восстановится; действие не продублируется.
                  </p>
                )}
                {!setStatus.isPaused &&
                  (error ||
                    (selected.requires_comment && !draft.comment.trim())) && (
                    <p
                      className="px-1 text-xs text-[var(--arm-red)]"
                      role="alert"
                    >
                      {error
                        ? error instanceof NetworkError
                          ? `${error.message} Нажмите ✓ ещё раз: действие не продублируется.`
                          : error.message
                        : selected.code === STATUS_REJECTED
                          ? "Для «Не принята» укажите причину отказа и кому передана информация."
                          : "Для этого статуса нужен комментарий с результатами."}
                    </p>
                  )}
              </div>
              <button
                type="button"
                aria-label="Сохранить статус"
                title="Сохранить (Ctrl+Enter)"
                onClick={submit}
                disabled={setStatus.isPending}
                className="flex size-8 items-center justify-center rounded-sm border-2 border-[var(--arm-orange)] text-[var(--arm-orange)] hover:bg-[#fff1ea] disabled:opacity-50"
              >
                <Check className="size-4" />
              </button>
              <button
                type="button"
                aria-label="Отменить"
                title="Отменить (Esc)"
                onClick={cancelEditor}
                className="flex size-8 items-center justify-center rounded-sm border border-[#a9adb2] hover:bg-[var(--arm-panel)]"
              >
                <X className="size-4" />
              </button>
            </div>
          )}

          <div
            className="flex items-stretch bg-[var(--arm-strip)] text-[var(--arm-on-dark)]"
            data-testid="services-strip"
          >
            <div className="flex w-[104px] items-center px-3 text-xs text-[var(--arm-on-dark-muted)]">
              Службы:
            </div>
            <div className="flex flex-wrap items-stretch">
            {ownService && (
              <div
                className={cn(
                  "flex w-[110px] flex-col border-r border-[var(--arm-dark)]",
                  panelOpen && "bg-[var(--arm-blue)]",
                )}
                data-testid="own-service-tab"
              >
                <div className="flex justify-center gap-1 py-0.5">
                  <button
                    type="button"
                    aria-label={
                      panelOpen ? "Свернуть службу" : "Раскрыть службу"
                    }
                    aria-expanded={panelOpen}
                    onClick={() => setPanelOpen((v) => !v)}
                    className="flex size-6 items-center justify-center rounded-sm border border-[var(--arm-blue)] text-white hover:bg-[var(--arm-blue-dark)]"
                  >
                    {panelOpen ? (
                      <ChevronDown className="size-3.5" />
                    ) : (
                      <ChevronUp className="size-3.5" />
                    )}
                  </button>
                  <button
                    type="button"
                    aria-label="Проставить статус"
                    title={finished ? "Карточка закрыта" : "Проставить статус"}
                    disabled={finished || transitions.length === 0}
                    onClick={() => openEditor()}
                    className="flex size-6 items-center justify-center rounded-sm border border-[var(--arm-orange)] text-[var(--arm-orange)] hover:bg-[var(--arm-orange)] hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Pencil className="size-3.5" />
                  </button>
                  <CallButton
                    service={ownService}
                    calls={callsByService.get(ownService.code) ?? []}
                    disabled={finished || Boolean(openCall) || callBusy}
                    onClick={() => dial(ownService.code)}
                  />
                </div>
                <div
                  className={cn(
                    "truncate px-2 text-center text-xs font-semibold",
                    ownService.is_main && "underline underline-offset-2",
                  )}
                  title={ownService.title}
                >
                  {ownService.short_title}
                </div>
                <div
                  className="truncate px-2 pb-1 text-center text-[10px] text-white/80"
                  title={ownService.status_title}
                >
                  {formatTime(ownService.at ?? attempt.issued_at, false)}{" "}
                  {ownService.status_title}
                </div>
              </div>
            )}
            {otherServices.map((s) => (
              <div
                key={s.code}
                className="flex w-[110px] flex-col justify-end border-r border-[var(--arm-dark)]"
              >
                <div className="flex justify-center py-0.5">
                  <CallButton
                    service={s}
                    calls={callsByService.get(s.code) ?? []}
                    disabled={finished || Boolean(openCall) || callBusy}
                    onClick={() => dial(s.code)}
                  />
                </div>
                <div
                  className={cn(
                    "truncate px-2 text-center text-xs font-semibold",
                    s.is_main && "underline underline-offset-2",
                  )}
                  title={s.title}
                >
                  {s.short_title}
                </div>
                <div className="truncate px-2 pb-1 text-center text-[10px] text-[var(--arm-on-dark-muted)]">
                  {formatTime(s.at ?? attempt.issued_at, false)}{" "}
                  {s.status_title}
                </div>
              </div>
            ))}
            </div>
            <div className="ml-auto flex items-center gap-1 px-2">
              <FlagButton
                field="services"
                flagged={flagsByField.has("services")}
                disabled={finished}
                onClick={() => toggleFlag("services")}
                className="size-8 border-[var(--arm-on-dark-muted)] text-[var(--arm-on-dark)] hover:bg-[var(--arm-dark-2)]"
              />
              <span
                className="flex size-8 items-center justify-center rounded-sm bg-[var(--arm-panel)] text-[var(--arm-text)]"
                aria-hidden
              >
                <MessageSquareWarning className="size-4" />
              </span>
              <Link
                to={`/student/sessions/${attempt.session.id}/journal`}
                aria-label="Закрыть карточку и вернуться в журнал"
                className="flex size-8 items-center justify-center rounded-sm bg-[var(--arm-panel)] text-[var(--arm-text)] hover:bg-[var(--arm-panel-2)]"
              >
                <X className="size-4" />
              </Link>
            </div>
          </div>
        </div>
      </div>

      <TrainerPanel
        connection={connection}
        footer={
          <button
            type="button"
            onClick={() => setShowHelp(true)}
            className="text-left text-xs text-[var(--arm-blue-dark)] underline-offset-2 hover:underline"
          >
            Горячие клавиши (?)
          </button>
        }
      >
        <div>
          <div className="text-xs text-[var(--arm-text-muted)]">
            Карточка {card.number}
          </div>
          <div className="font-medium leading-tight">
            {attempt.session.title}
          </div>
        </div>
        <div className="rounded-sm bg-[var(--arm-field)] p-2">
          <div className="flex items-baseline justify-between text-xs text-[var(--arm-text-muted)]">
            <span>Норматив {attempt.norm_seconds} с</span>
            <TimerBadge
              issuedAt={attempt.issued_at}
              primaryStatusAt={attempt.primary_status_at}
              normSeconds={attempt.norm_seconds}
              now={now}
              active={!finished}
              className="text-base [&[data-phase=ok]]:text-[var(--arm-text)]"
            />
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded bg-[#cfd2d4]">
            <div
              className={cn(
                "h-full transition-[width]",
                timer.phase === "ok" && "bg-[var(--arm-blue)]",
                timer.phase === "warning" && "bg-[var(--arm-warning)]",
                (timer.phase === "overdue" || timer.phase === "late") &&
                  "bg-[var(--arm-red)]",
                timer.phase === "done" && "bg-[var(--arm-green)]",
              )}
              style={{ width: `${Math.round(timer.progress * 100)}%` }}
            />
          </div>
          <div className="mt-1 text-xs">
            {attempt.primary_status_at
              ? timer.phase === "late"
                ? `Отставание ${formatSeconds(timer.elapsed - attempt.norm_seconds)}`
                : "Первичный статус в норматив"
              : timer.phase === "overdue"
                ? `Отставание ${formatSeconds(-timer.remaining)}`
                : "Ожидается первичный статус"}
          </div>
        </div>
        <div className="text-xs">
          <span className="text-[var(--arm-text-muted)]">Статус службы: </span>
          <b>{attempt.response_status_title}</b>
          {attempt.card_status_alert && (
            <span className="ml-2 font-semibold text-[var(--arm-red)]">
              {attempt.card_status_title}
            </span>
          )}
        </div>
        {attempt.session.hints_enabled && (
          <div className="flex flex-col gap-1">
            <label className="inline-flex items-center gap-2 text-xs text-[var(--arm-text-muted)]">
              <input
                type="checkbox"
                checked={hintsOn}
                onChange={(e) => setHintsOn(e.target.checked)}
              />{" "}
              подсказки
            </label>
            {hintsOn && !finished && (
              <p className="rounded-sm bg-[var(--arm-field)] p-2 text-xs leading-snug">
                {hintFor(attempt)}
              </p>
            )}
            {hintsOn && !finished && (
              <p
                className="rounded-sm bg-[var(--arm-field)] p-2 text-xs leading-snug"
                data-testid="data-check-hint"
              >
                Проверьте данные карточки: оператор 112 мог ошибиться в адресе,
                типе, пострадавших или службах. Сверьте поля с описанием со слов
                заявителя и отметьте ошибку флажком у поля.
              </p>
            )}
          </div>
        )}
        {shownCall && (
          <ServiceCallPanel
            call={shownCall}
            telephony={Boolean(
              shownCall.telephony && softphone?.mode === "sip",
            )}
            sttAvailable={sttAvailable}
            micDeviceId={softphone?.micDeviceId ?? null}
            devices={softphone?.devices ?? []}
            onMicDevice={(id) => softphone?.setMicDevice(id)}
            onMicOpened={() => void softphone?.refreshDevices()}
            pending={callBusy}
            error={callError ? callError.message : null}
            onSay={(text, actionId) =>
              sayToOfficer.mutate({ callId: shownCall.id, text, actionId })
            }
            onSpeak={(blob, actionId) =>
              speakToOfficer.mutate({ callId: shownCall.id, blob, actionId })
            }
            onAnswer={answerReport}
            onEnd={hangUpService}
          />
        )}
        {attempt.flagged_fields.length > 0 && (
          <div className="text-xs" data-testid="flag-count">
            <span className="text-[var(--arm-text-muted)]">
              Отмечено ошибок оператора:{" "}
            </span>
            <b>{attempt.flagged_fields.length}</b>
          </div>
        )}
        {finished ? (
          <div
            className={cn(
              "flex flex-col gap-2 rounded-sm border p-2 text-xs",
              evaluation?.passed === false
                ? "border-[var(--arm-red)] bg-[#fdeeed]"
                : "border-[var(--arm-green)] bg-[#eef8f0]",
            )}
            role="status"
          >
            <b>Работа с карточкой завершена</b>
            <span>
              Последний статус:{" "}
              {lastEntry?.title ?? attempt.response_status_title}
              {attempt.submitted_at
                ? `, ${formatTime(attempt.submitted_at)}`
                : ""}
              .
            </span>
            {evaluation ? (
              <div className="flex items-baseline gap-2">
                <span
                  className="text-2xl font-semibold tabular-nums"
                  data-testid="card-score"
                >
                  {evaluation.total}
                </span>
                <span
                  className={
                    evaluation.passed
                      ? "font-semibold text-[var(--arm-green)]"
                      : "font-semibold text-[var(--arm-red)]"
                  }
                >
                  {evaluation.passed ? "Зачтено" : "Не зачтено"}
                </span>
              </div>
            ) : (
              <span className="text-[var(--arm-text-muted)]">
                Оценка считается…
              </span>
            )}
            <Link
              to={`/student/attempts/${attempt.id}/review`}
              className="text-[var(--arm-blue-dark)] underline-offset-2 hover:underline"
            >
              Открыть разбор
            </Link>
            {nextId ? (
              <ArmButton
                variant="blue"
                className="normal-case"
                onClick={() => navigate(`/student/attempts/${nextId}`)}
              >
                Следующая карточка
              </ArmButton>
            ) : (
              <ArmButton
                variant="blue"
                className="normal-case"
                onClick={() =>
                  navigate(`/student/sessions/${attempt.session.id}/journal`)
                }
              >
                В журнал
              </ArmButton>
            )}
          </div>
        ) : confirmFinish ? (
          <div
            className="mt-auto flex flex-col gap-2 rounded-sm border border-[var(--arm-orange)] bg-[#fff1ea] p-2 text-xs"
            role="alertdialog"
            aria-label="Подтверждение"
          >
            <span>
              Завершить работу с карточкой? Дальше статусы поставить будет
              нельзя.
            </span>
            <div className="flex gap-2">
              <ArmButton
                variant="orange"
                className="normal-case"
                disabled={finish.isPending}
                onClick={() =>
                  finish.mutate(undefined, {
                    onSettled: () => setConfirmFinish(false),
                  })
                }
              >
                Да, завершить
              </ArmButton>
              <ArmButton
                className="normal-case"
                onClick={() => setConfirmFinish(false)}
              >
                Нет
              </ArmButton>
            </div>
          </div>
        ) : (
          <ArmButton
            variant="dark"
            className="mt-auto normal-case"
            onClick={() => setConfirmFinish(true)}
          >
            Завершить работу с карточкой
          </ArmButton>
        )}
        {finish.error && (
          <p className="text-xs text-[var(--arm-red)]" role="alert">
            {finish.error.message}
          </p>
        )}
      </TrainerPanel>

      {showHelp && <HotkeysHelp onClose={() => setShowHelp(false)} />}
    </div>
  );
}

/** «Позвонить» a service officer from the strip (issue #36); shows how many calls were made. */
function CallButton({
  service,
  calls,
  disabled,
  onClick,
}: {
  service: { code: string; title: string };
  calls: AttemptOut["service_calls"];
  disabled: boolean;
  onClick: () => void;
}) {
  const open = calls.some((c) => c.ended_at === null);
  return (
    <button
      type="button"
      aria-label={`Позвонить: ${service.title}`}
      title={open ? "Идёт разговор" : `Позвонить дежурному: ${service.title}`}
      disabled={disabled}
      onClick={onClick}
      data-service={service.code}
      className={cn(
        "relative flex size-6 items-center justify-center rounded-sm border text-white disabled:cursor-not-allowed disabled:opacity-40",
        open
          ? "border-[var(--arm-green)] bg-[var(--arm-green)]"
          : "border-[var(--arm-green)] text-[var(--arm-green)] hover:bg-[var(--arm-green)] hover:text-white",
      )}
    >
      <Phone className="size-3.5" aria-hidden />
      {calls.length > 0 && (
        <span className="absolute -right-1 -top-1 rounded-full bg-white px-1 text-[9px] font-semibold text-[var(--arm-text)]">
          {calls.length}
        </span>
      )}
    </button>
  );
}

function PhoneBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-1.5 bg-[var(--arm-panel)] px-2 py-1">
      <Phone className="size-4 text-[var(--arm-text-muted)]" aria-hidden />
      <div className="flex w-[7.5rem] flex-col">
        <span className="text-[9px] text-[var(--arm-text-muted)]">{label}</span>
        <span className="border-b border-[#a9adb2] text-sm tabular-nums">
          {value || " "}
        </span>
      </div>
      <MessageSquare
        className="size-4 text-[var(--arm-text-muted)]"
        aria-hidden
      />
    </div>
  );
}

function FlagBox({
  active,
  label,
  children,
}: {
  active: boolean;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex h-7 items-center gap-1 rounded-sm border px-2 text-xs font-semibold",
        active
          ? "border-[var(--arm-red)] bg-[var(--arm-red)] text-white"
          : "border-[#a9adb2] text-[var(--arm-text)]",
      )}
      aria-label={`${label}: ${active ? "да" : "нет"}`}
    >
      {label} {children}
    </span>
  );
}

function HotkeysHelp({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-label="Горячие клавиши"
      onClick={onClose}
    >
      <div
        className="w-96 rounded-sm bg-white p-4 text-sm shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <b>Горячие клавиши</b>
          <button type="button" aria-label="Закрыть" onClick={onClose}>
            <X className="size-4" />
          </button>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
          <dt>
            <kbd className="rounded border px-1 font-mono text-xs">Alt+A</kbd>
          </dt>
          <dd>«Принята»</dd>
          <dt>
            <kbd className="rounded border px-1 font-mono text-xs">Alt+R</kbd>
          </dt>
          <dd>«Не принята»</dd>
          <dt>
            <kbd className="rounded border px-1 font-mono text-xs">
              Ctrl+Enter
            </kbd>
          </dt>
          <dd>сохранить статус</dd>
          <dt>
            <kbd className="rounded border px-1 font-mono text-xs">Esc</kbd>
          </dt>
          <dd>отменить ввод</dd>
          <dt>
            <kbd className="rounded border px-1 font-mono text-xs">Tab</kbd>
          </dt>
          <dd>переход по полям</dd>
          <dt>
            <kbd className="rounded border px-1 font-mono text-xs">?</kbd>
          </dt>
          <dd>эта подсказка</dd>
        </dl>
      </div>
    </div>
  );
}
