import { useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  Bookmark,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Clipboard,
  Headphones,
  Link2,
  Search,
  Zap,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";

import { journalKey, useJournal, type JournalItem } from "@/api/training";
import { ErrorState, LoadingState } from "@/components/states";
import { clockParts, formatDate, formatLongDate, formatTime, useNow } from "@/emulator/time";
import { ArmButton, TimerBadge, Toggle, TrainerPanel } from "@/emulator/widgets";
import { useSessionEvents } from "@/emulator/ws";
import { cn } from "@/lib/utils";

const PAGE_SIZES = [10, 20, 50] as const;
const ACTIVE_STATES = new Set(["issued", "received", "in_progress"]);

// Tabs of the real workstation header (screenshot page 12); only «журнал» works here.
const HEADER_TABS = ["журнал", "экран", "статистика", "УЕР", "БДПН", "вики", "заявители", "техника", "аудит", "отчеты", "контроль", "смена", "регионы"];

export function JournalPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  if (!sessionId) return <Navigate to="/student" replace />;
  return <Journal sessionId={sessionId} />;
}

function Journal({ sessionId }: { sessionId: string }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState<(typeof PAGE_SIZES)[number]>(10);
  const [search, setSearch] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [pendingUpdates, setPendingUpdates] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const now = useNow();
  const query = useJournal(sessionId, page, perPage);

  const onEvent = useCallback(() => {
    if (autoRefresh) void client.invalidateQueries({ queryKey: journalKey(sessionId) });
    else setPendingUpdates((n) => n + 1);
  }, [autoRefresh, client, sessionId]);
  const connection = useSessionEvents(sessionId, query.data?.last_seq, onEvent);

  const items = useMemo(() => {
    const list = query.data?.items ?? [];
    const needle = search.trim().toLowerCase();
    if (!needle) return list;
    return list.filter((i) =>
      [i.card_number, i.incident_title, i.address, i.caller.name, i.description, i.card_status_title]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [query.data, search]);

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function refreshNow() {
    setPendingUpdates(0);
    void client.invalidateQueries({ queryKey: journalKey(sessionId) });
  }

  if (query.isPending) return <LoadingState text="Открываем журнал…" />;
  if (query.isError || !query.data) {
    return (
      <div className="p-6">
        <ErrorState message={query.error?.message ?? "Не удалось открыть журнал."} onRetry={() => void query.refetch()} />
      </div>
    );
  }
  const data = query.data;
  const total = data.total;
  const from = total === 0 ? 0 : (page - 1) * perPage + 1;
  const to = Math.min(total, page * perPage);
  const pages = Math.max(1, Math.ceil(total / perPage));
  const clock = clockParts(now);
  const activeCount = data.items.filter((i) => ACTIVE_STATES.has(i.state)).length;

  return (
    <div className="arm flex min-h-dvh min-w-[1280px]">
      <div className="flex min-w-0 flex-1 flex-col gap-2 p-2">
        {/* Header: search on the left, date / clock / operator on the right (screenshot page 12). */}
        <header className="flex gap-1">
          <div className="flex flex-1 flex-col justify-between bg-[var(--arm-panel)] px-4 py-2">
            <div className="flex items-center gap-3">
              <label htmlFor="journal-search" className="text-2xl text-[var(--arm-text)]">
                Поиск происшествий
              </label>
              <input
                id="journal-search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="номер, тип, адрес, заявитель"
                className="h-8 flex-1 border-b border-[#a9adb2] bg-transparent px-2 text-base focus:border-[var(--arm-blue)] focus:outline-none"
              />
              <Search className="size-5 text-[var(--arm-text-muted)]" aria-hidden />
            </div>
            <div className="flex items-center justify-between text-xs text-[var(--arm-text-muted)]">
              <button type="button" className="inline-flex items-center gap-1 hover:text-[var(--arm-text)]">
                расширенный по параметрам <ChevronDown className="size-3" aria-hidden />
              </button>
              <ArmButton className="h-6 px-2 normal-case" onClick={() => setSearch("")}>
                сбросить
              </ArmButton>
            </div>
          </div>
          <div className="flex w-[520px] flex-col bg-[var(--arm-dark)] text-[var(--arm-on-dark)]">
            <div className="flex items-start justify-between px-3 pt-2">
              <div>
                <div className="text-sm font-medium">{formatLongDate(now)}</div>
                <div className="text-xs text-[var(--arm-on-dark-muted)]">
                  оп. {data.arm.operator_no}, {data.arm.dispatcher} · АРМ {data.arm.arm_no}
                </div>
                <div className="mt-1 flex items-center gap-1 text-[10px] text-[var(--arm-on-dark-muted)]">
                  <Headphones className="size-3" aria-hidden /> Подключение: {data.session.service?.short_title ?? "ДДС"}
                </div>
              </div>
              <div className="flex items-start font-mono leading-none" aria-label="Текущее время">
                <span className="text-5xl font-medium tabular-nums">{clock.hm}</span>
                <span className="mt-1 text-lg tabular-nums text-[var(--arm-on-dark-muted)]">:{clock.s}</span>
              </div>
            </div>
            <nav className="mt-auto flex gap-px bg-[var(--arm-dark-2)] px-1 pt-1 text-[9px] uppercase text-[var(--arm-on-dark-muted)]" aria-label="Разделы АРМ">
              {HEADER_TABS.map((tab) => (
                <span
                  key={tab}
                  className={cn("flex-1 rounded-t-sm px-1 py-1 text-center", tab === "журнал" && "bg-[var(--arm-blue)] text-white")}
                >
                  {tab}
                </span>
              ))}
            </nav>
          </div>
          <ArmButton
            variant="orange"
            className="h-auto w-32 flex-col items-start whitespace-normal px-3 py-2 text-left text-sm leading-tight normal-case"
            disabled
            title="В режиме ДДС карточки создаёт оператор 112"
          >
            создать новую карточку
          </ArmButton>
        </header>

        {/* Incident list. */}
        <section className="flex flex-1 flex-col bg-[var(--arm-dark)] text-[var(--arm-on-dark)]">
          <div className="flex items-center gap-4 px-4 py-2">
            <h1 className="inline-flex items-center gap-1 text-base font-medium">
              Список происшествий <ChevronUp className="size-4" aria-hidden />
            </h1>
            <div className="ml-auto flex items-center gap-4 text-xs">
              <button
                type="button"
                className="inline-flex items-center gap-1 text-[var(--arm-on-dark-muted)] hover:text-[var(--arm-on-dark)]"
                onClick={refreshNow}
                title="Обновить список"
              >
                <Bell className="size-3.5" aria-hidden /> уведомления
                {pendingUpdates > 0 && (
                  <span className="rounded-full bg-[var(--arm-orange)] px-1.5 text-[10px] text-white">{pendingUpdates}</span>
                )}
              </button>
              <Toggle checked={autoRefresh} onChange={(v) => { setAutoRefresh(v); if (v) refreshNow(); }} label="автообновление" />
              <Toggle checked={false} onChange={() => undefined} label="обращения в очереди" disabled />
              <span className="rounded-sm bg-[var(--arm-dark-2)] px-2 py-1 text-[var(--arm-on-dark-muted)]">выберите что показать ▾</span>
            </div>
          </div>

          <table className="w-full border-separate border-spacing-y-1 px-2 text-xs">
            <thead className="text-[10px] text-[var(--arm-on-dark-muted)]">
              <tr>
                <th className="w-6" aria-label="Раскрыть" />
                <th className="w-6 font-normal">Связи</th>
                <th className="w-6 font-normal">ЧС</th>
                <th className="w-5" aria-label="Закрепить" />
                <th className="w-5" aria-label="Важное" />
                <th className="w-16 font-normal">Таймер</th>
                <th className="w-9 font-normal">Опер.</th>
                <th className="w-9 font-normal">АРМ</th>
                <th className="w-[4.5rem] font-normal">Номер</th>
                <th className="w-14 font-normal">Дата ↓</th>
                <th className="w-[4.5rem] font-normal">Время</th>
                <th className="w-44 text-left font-normal">Тип происшествия</th>
                <th className="w-10 font-normal">Постр.</th>
                <th className="w-24 text-left font-normal">Статус</th>
                <th className="text-left font-normal">Адрес</th>
                <th className="w-7 font-normal">Контр.</th>
                <th className="w-7 font-normal">Пров.</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr>
                  <td colSpan={17} className="bg-[var(--arm-row)] px-4 py-6 text-center text-sm text-[var(--arm-on-dark-muted)]">
                    {search
                      ? "По запросу ничего не найдено. Измените условия поиска или нажмите «сбросить»."
                      : data.session.status === "running"
                        ? "Карточек пока нет. Новые карточки появятся здесь, как только оператор 112 направит их в вашу службу."
                        : data.session.status === "finished"
                          ? "Занятие завершено: новые карточки не выдаются."
                          : "Занятие ещё не начато. Как только преподаватель его запустит, карточки появятся здесь сами."}
                  </td>
                </tr>
              )}
              {items.map((item) => (
                <JournalRow
                  key={item.attempt_id}
                  item={item}
                  now={now}
                  expanded={expanded.has(item.attempt_id)}
                  onToggle={() => toggleExpanded(item.attempt_id)}
                  onOpen={() => navigate(`/student/attempts/${item.attempt_id}`)}
                />
              ))}
            </tbody>
          </table>

          <div className="mt-auto flex items-center justify-between px-4 py-2 text-xs text-[var(--arm-on-dark-muted)]">
            <span className="rounded-sm bg-[var(--arm-dark-2)] px-2 py-1">выберите группу ▾</span>
            <div className="flex items-center gap-3">
              <span>
                Страница:{" "}
                <select
                  aria-label="Страница"
                  value={page}
                  onChange={(e) => setPage(Number(e.target.value))}
                  className="rounded-sm bg-[var(--arm-dark-2)] px-1 py-0.5 text-[var(--arm-on-dark)]"
                >
                  {Array.from({ length: pages }, (_, i) => i + 1).map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </span>
              <span>
                Записей на странице:{" "}
                <select
                  aria-label="Записей на странице"
                  value={perPage}
                  onChange={(e) => { setPerPage(Number(e.target.value) as (typeof PAGE_SIZES)[number]); setPage(1); }}
                  className="rounded-sm bg-[var(--arm-dark-2)] px-1 py-0.5 text-[var(--arm-on-dark)]"
                >
                  {PAGE_SIZES.map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
              </span>
              <span>
                {from}-{to} из {total}
              </span>
              <button type="button" aria-label="Предыдущая страница" disabled={page <= 1} onClick={() => setPage(page - 1)} className="disabled:opacity-40">
                <ChevronLeft className="size-4" />
              </button>
              <button type="button" aria-label="Следующая страница" disabled={page >= pages} onClick={() => setPage(page + 1)} className="disabled:opacity-40">
                <ChevronRight className="size-4" />
              </button>
            </div>
          </div>
        </section>
      </div>

      <TrainerPanel connection={connection}>
        <div>
          <div className="text-xs text-[var(--arm-text-muted)]">Занятие</div>
          <div className="font-medium leading-tight">{data.session.title}</div>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-[var(--arm-text-muted)]">Служба</dt>
          <dd>{data.session.service?.title ?? "—"}</dd>
          <dt className="text-[var(--arm-text-muted)]">Норматив</dt>
          <dd>{data.session.norm_seconds} с на первичный статус</dd>
          <dt className="text-[var(--arm-text-muted)]">В работе</dt>
          <dd>{activeCount} из {total}</dd>
        </dl>
        {data.session.hints_enabled && (
          <p className="rounded-sm bg-[var(--arm-field)] p-2 text-xs leading-snug text-[var(--arm-text-muted)]">
            Норматив идёт с момента, когда карточка появилась в журнале. Откройте её из списка и поставьте первичный
            статус («Принята» или «Не принята») в течение норматива, иначе карточка станет «Не оповещено».
          </p>
        )}
      </TrainerPanel>
    </div>
  );
}

function JournalRow({
  item,
  now,
  expanded,
  onToggle,
  onOpen,
}: {
  item: JournalItem;
  now: number;
  expanded: boolean;
  onToggle: () => void;
  onOpen: () => void;
}) {
  const active = ACTIVE_STATES.has(item.state);
  const cell = "bg-[var(--arm-row)] px-1 py-1.5 align-middle";
  return (
    <>
      <tr
        className="cursor-pointer text-[var(--arm-on-dark)] hover:[&>td]:bg-[var(--arm-row-hover)]"
        data-attempt={item.attempt_id}
        data-card-status={item.card_status}
        onClick={onOpen}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onOpen();
          }
        }}
        tabIndex={0}
        role="link"
        aria-label={`Открыть карточку ${item.card_number}: ${item.incident_title}`}
      >
        <td className={cn(cell, "text-center")}>
          <button
            type="button"
            aria-expanded={expanded}
            aria-label={expanded ? "Свернуть" : "Раскрыть"}
            onClick={(e) => { e.stopPropagation(); onToggle(); }}
            className="rounded-sm p-0.5 hover:bg-[var(--arm-dark)]"
          >
            {expanded ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
          </button>
        </td>
        <td className={cn(cell, "text-center")}><Link2 className="mx-auto size-3.5 text-[var(--arm-on-dark-muted)]" aria-hidden /></td>
        <td className={cn(cell, "text-center")} />
        <td className={cn(cell, "text-center")}><Bookmark className="mx-auto size-3.5 text-[var(--arm-on-dark-muted)]" aria-hidden /></td>
        <td className={cn(cell, "text-center")}><Zap className="mx-auto size-3.5 text-[var(--arm-on-dark-muted)]" aria-hidden /></td>
        <td className={cn(cell, "text-center")}>
          <TimerBadge issuedAt={item.issued_at} primaryStatusAt={item.primary_status_at} normSeconds={item.norm_seconds} now={now} active={active} />
        </td>
        <td className={cn(cell, "text-center text-[var(--arm-on-dark-muted)]")}>{item.operator_no}</td>
        <td className={cn(cell, "text-center text-[var(--arm-on-dark-muted)]")}>{item.arm_no}</td>
        <td className={cn(cell, "text-center font-mono")}>{item.card_number}</td>
        <td className={cn(cell, "text-center text-[var(--arm-on-dark-muted)]")}>{formatDate(item.issued_at)}</td>
        <td className={cn(cell, "text-center font-mono text-base font-medium")}>
          {formatTime(item.issued_at, false)}
          <sup className="text-[10px] font-normal">{formatTime(item.issued_at).slice(-2)}</sup>
        </td>
        <td className={cn(cell, "text-[13px] leading-tight font-semibold")}>{item.incident_title || "—"}</td>
        <td className={cn(cell, "text-center")}>{item.injured ? "Да" : "Нет"}</td>
        <td className={cn(cell, item.card_status_alert ? "font-semibold text-[var(--arm-red)]" : "text-[var(--arm-on-dark-muted)]")}>
          {item.card_status_title}
        </td>
        <td className={cn(cell, "text-[11px] leading-tight text-[var(--arm-on-dark)]")}>{item.address}</td>
        <td className={cn(cell, "text-center")}><Clipboard className="mx-auto size-3.5 text-[var(--arm-on-dark-muted)]" aria-hidden /></td>
        <td className={cn(cell, "text-center")}>
          <span className={cn("mx-auto flex size-4 items-center justify-center rounded-full", item.state === "finished" ? "bg-[var(--arm-green)]" : "bg-[#5b6168]")}>
            <Check className="size-3 text-white" aria-hidden />
          </span>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={17} className="bg-[var(--arm-dark-2)] px-4 py-2 text-xs text-[var(--arm-on-dark)]">
            <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
              <span className="text-[var(--arm-on-dark-muted)]">Службы:</span>
              <span className="flex flex-wrap gap-x-4">
                {item.services.map((s) => (
                  <span key={s.code} className={cn(s.is_own && "font-semibold")}>
                    {s.short_title} — {s.status_title}
                    {s.at ? ` ${formatTime(s.at)}` : ""}
                  </span>
                ))}
              </span>
              <span className="text-[var(--arm-on-dark-muted)]">Заявитель:</span>
              <span>
                {item.caller.name || "—"}
                {item.caller.role ? `, ${item.caller.role}` : ""}
                {item.caller.phone ? `, ${item.caller.phone}` : ""}
              </span>
              <span className="text-[var(--arm-on-dark-muted)]">Информация:</span>
              <span>{[item.incident_group, ...item.signs].filter(Boolean).join(" · ") || "—"}</span>
              <span className="text-[var(--arm-on-dark-muted)]">Описание:</span>
              <span>
                {formatDate(item.issued_at)} {formatTime(item.issued_at)} Опер. {item.operator_no} —{" "}
                <span className="font-semibold">{item.description}</span>
              </span>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
