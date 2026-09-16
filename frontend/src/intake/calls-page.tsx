import { useQueryClient } from "@tanstack/react-query";
import { PhoneIncoming, PhoneMissed } from "lucide-react";
import { useCallback, useEffect } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { journalKey, useJournal, type JournalItem } from "@/api/training";
import { ErrorState, LoadingState } from "@/components/states";
import { clockParts, formatLongDate, formatTime, useNow } from "@/emulator/time";
import { ArmButton, TrainerPanel } from "@/emulator/widgets";
import { useSessionEvents, type SessionEvent } from "@/emulator/ws";

const ACTIVE = new Set(["issued", "received", "in_progress"]);

/**
 * The operator-112 workplace between calls (`/student/sessions/:id/calls`): opening it asks
 * for the next call of the queue; when one rings the card opens by itself. Handled calls are
 * listed with their scores.
 */
export function CallsPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  if (!sessionId) return <Navigate to="/student" replace />;
  return <Calls sessionId={sessionId} />;
}

function Calls({ sessionId }: { sessionId: string }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const now = useNow();
  const query = useJournal(sessionId, 1, 20);

  const onEvent = useCallback(
    (event: SessionEvent) => {
      if (event.type === "attempt.issued" && typeof event.payload.attempt_id === "string") {
        navigate(`/student/attempts/${event.payload.attempt_id}`);
        return;
      }
      void client.invalidateQueries({ queryKey: journalKey(sessionId) });
    },
    [client, navigate, sessionId],
  );
  const connection = useSessionEvents(sessionId, query.data?.last_seq, onEvent);

  const current = query.data?.items.find((i) => ACTIVE.has(i.state));
  useEffect(() => {
    if (current) navigate(`/student/attempts/${current.attempt_id}`, { replace: true });
  }, [current, navigate]);

  if (query.isPending) return <LoadingState text="Подключаем рабочее место…" />;
  if (query.isError || !query.data) {
    return (
      <div className="p-6">
        <ErrorState message={query.error?.message ?? "Не удалось открыть рабочее место."} onRetry={() => void query.refetch()} />
      </div>
    );
  }
  const data = query.data;
  const done = data.items.filter((i) => !ACTIVE.has(i.state));
  const running = data.session.status === "running";
  const finished = data.session.status === "finished";
  const clock = clockParts(now);

  return (
    <div className="arm flex min-h-dvh min-w-[1280px]">
      <div className="flex min-w-0 flex-1 flex-col gap-2 p-2">
        <header className="flex gap-1">
          <div className="flex flex-1 items-center bg-[var(--arm-panel)] px-4 py-2 text-2xl">Рабочее место оператора 112</div>
          <div className="flex flex-col items-end justify-center bg-[var(--arm-panel)] px-4 py-1 text-right text-xs">
            <span>{formatLongDate(now)}</span>
            <span className="font-mono text-lg tabular-nums">
              {clock.hm}
              <span className="text-[var(--arm-text-muted)]">:{clock.s}</span>
            </span>
          </div>
          <div className="flex flex-col justify-center bg-[var(--arm-panel)] px-4 py-1 text-xs">
            <span>{data.arm.dispatcher}</span>
            <span className="text-[var(--arm-text-muted)]">
              Опер. {data.arm.operator_no}, АРМ {data.arm.arm_no}
            </span>
          </div>
        </header>

        <div className="flex flex-1 flex-col items-center justify-center gap-3 bg-[var(--arm-panel)] p-6 text-center" role="status" aria-live="polite" data-testid="call-waiting">
          {finished ? (
            <>
              <PhoneMissed className="size-10 text-[var(--arm-text-muted)]" aria-hidden />
              <p className="text-lg">Занятие завершено</p>
            </>
          ) : running && !current && done.length > 0 ? (
            // Opening the page asks for the next call; none came, so the queue is done.
            <>
              <PhoneMissed className="size-10 text-[var(--arm-text-muted)]" aria-hidden />
              <p className="text-lg">Вызовов больше нет: очередь занятия пройдена</p>
              <ArmButton variant="blue" className="normal-case" onClick={() => void query.refetch()}>
                Проверить ещё раз
              </ArmButton>
            </>
          ) : running ? (
            <>
              <PhoneIncoming className="size-10 animate-pulse text-[var(--arm-orange)]" aria-hidden />
              <p className="text-lg">Ожидание вызова…</p>
              <p className="text-xs text-[var(--arm-text-muted)]">Как только поступит вызов, откроется карточка происшествия.</p>
              <ArmButton variant="blue" className="normal-case" onClick={() => void query.refetch()}>
                Проверить сейчас
              </ArmButton>
            </>
          ) : (
            <>
              <PhoneMissed className="size-10 text-[var(--arm-text-muted)]" aria-hidden />
              <p className="text-lg">Занятие ещё не начато</p>
              <p className="text-xs text-[var(--arm-text-muted)]">Когда преподаватель запустит занятие, первый вызов придёт сюда.</p>
            </>
          )}
        </div>

        <section className="bg-[var(--arm-panel)]">
          <h2 className="px-4 py-2 text-sm font-semibold">Принятые вызовы</h2>
          {done.length === 0 ? (
            <p className="px-4 pb-3 text-xs text-[var(--arm-text-muted)]">Пока ни одного.</p>
          ) : (
            <table className="w-full text-xs">
              <thead className="bg-[var(--arm-dark)] text-left text-[var(--arm-on-dark)]">
                <tr>
                  <th className="px-4 py-1 font-normal">Происшествие №</th>
                  <th className="px-4 py-1 font-normal">Поступил</th>
                  <th className="px-4 py-1 font-normal">Сохранён</th>
                  <th className="px-4 py-1 font-normal">Балл</th>
                  <th className="px-4 py-1 font-normal" />
                </tr>
              </thead>
              <tbody>
                {done.map((i) => (
                  <CallRow key={i.attempt_id} item={i} />
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
      <TrainerPanel connection={connection}>
        <div>
          <div className="text-xs text-[var(--arm-text-muted)]">Занятие</div>
          <div className="font-medium leading-tight">{data.session.title}</div>
        </div>
        <p className="text-xs text-[var(--arm-text-muted)]">
          Норматив {data.session.norm_seconds} с на приём вызова. Принято вызовов: {done.length}.
        </p>
      </TrainerPanel>
    </div>
  );
}

function CallRow({ item }: { item: JournalItem }) {
  return (
    <tr className="border-t border-[#dcdedf]">
      <td className="px-4 py-1 font-semibold">{item.card_number}</td>
      <td className="px-4 py-1">{formatTime(item.issued_at)}</td>
      <td className="px-4 py-1">{formatTime(item.submitted_at)}</td>
      <td className="px-4 py-1">
        <span className="text-[var(--arm-text-muted)]">{item.state === "evaluated" ? "см. разбор" : "считается…"}</span>
      </td>
      <td className="px-4 py-1 text-right">
        <Link to={`/student/attempts/${item.attempt_id}/review`} className="text-[var(--arm-blue-dark)] underline-offset-2 hover:underline">
          Разбор
        </Link>
      </td>
    </tr>
  );
}
