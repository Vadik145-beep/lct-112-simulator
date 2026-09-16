import { ArrowRight, BookOpen, Check, CircleAlert, X } from "lucide-react";
import { Link, Navigate, useParams } from "react-router-dom";

import { useAttempt, type AttemptOut } from "@/api/training";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatSeconds, formatTime } from "@/emulator/time";
import { cn } from "@/lib/utils";

// Shapes of `attempt.evaluation` (EvaluationResult.to_dict() of the evaluation engine).
interface Component {
  key: string;
  title: string;
  score: number;
  max: number;
  status: "checked" | "not_checked" | "disabled";
  items: Record<string, unknown>[];
}
interface ErrorItem {
  code: string;
  title: string;
  penalty: number;
  explanation: string;
  memo_ref: string | null;
  critical: boolean;
}
interface Evaluation {
  total: number;
  passed: boolean;
  components: Record<string, Component>;
  errors: ErrorItem[];
  methods: Record<string, string>;
  ai_comment: string | null;
  checked_text?: string;
}
interface ReferenceStep {
  status: string;
  order_number?: boolean;
  comment_example?: string | null;
}
interface Reference {
  decision: "accept" | "reject";
  reject_reason: string | null;
  status_chain: ReferenceStep[];
  critical_errors: string[];
}

const STATUS_TITLES: Record<string, string> = {
  added: "Добавлена",
  received: "Получена службой",
  accepted: "Принята",
  rejected: "Не принята",
  response_started: "Начало реагирования",
  arrived: "Прибытие",
  works_started: "Проведение работ",
  works_done: "Работы завершены",
  works_refused: "Отказ от выполнения работ",
};
const DECISION_TITLES = { accept: "Принята", reject: "Не принята" };
const COMPONENT_ORDER = ["decision", "time", "status_chain", "comments", "typical_errors", "grammar"];

export function AttemptReviewPage() {
  const { attemptId } = useParams<{ attemptId: string }>();
  if (!attemptId) return <Navigate to="/student" replace />;
  return <Review attemptId={attemptId} />;
}

function Review({ attemptId }: { attemptId: string }) {
  const query = useAttempt(attemptId);
  if (query.isPending) return <LoadingState text="Готовим разбор…" />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const attempt = query.data;
  const evaluation = attempt.evaluation as Evaluation | null;
  if (!evaluation) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold">Разбор карточки {attempt.card.number}</h1>
        <p className="text-muted-foreground">
          Карточка ещё в работе: разбор появится, когда вы поставите финальный статус или нажмёте «Завершить работу с карточкой».
        </p>
        <Button asChild variant="outline">
          <Link to={`/student/attempts/${attempt.id}`}>Вернуться к карточке</Link>
        </Button>
      </div>
    );
  }
  return <ReviewView attempt={attempt} evaluation={evaluation} />;
}

function ReviewView({ attempt, evaluation }: { attempt: AttemptOut; evaluation: Evaluation }) {
  const reference = attempt.reference as Reference | null;
  const components = COMPONENT_ORDER.map((k) => evaluation.components[k]).filter((c): c is Component => Boolean(c));
  const dispatcherLog = attempt.status_log.filter((e) => e.by !== "system");
  const referenceChain = reference?.status_chain ?? [];
  const actualCodes = dispatcherLog.map((e) => e.status);
  const missing = referenceChain.filter((s) => !actualCodes.includes(s.status)).map((s) => s.status);
  const extra = actualCodes.filter((c) => !referenceChain.some((s) => s.status === c));
  const grammar = evaluation.components.grammar;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-sm text-muted-foreground">Разбор · карточка {attempt.card.number}</p>
          <h1 className="text-2xl font-semibold">{attempt.card.incident.final_title || "Карточка"}</h1>
          <p className="text-sm text-muted-foreground">{attempt.card.address.text}</p>
        </div>
        <div className={cn("flex items-center gap-4 rounded-xl border px-5 py-3", evaluation.passed ? "border-success/50 bg-success/10" : "border-destructive/50 bg-destructive/10")}>
          <div className="text-4xl font-semibold tabular-nums" data-testid="review-total">{evaluation.total}</div>
          <div className="leading-tight">
            <div className="text-xs text-muted-foreground">из 100</div>
            <div className={cn("font-semibold", evaluation.passed ? "text-success" : "text-destructive")} data-testid="review-verdict">
              {evaluation.passed ? "Зачтено" : "Не зачтено"}
            </div>
          </div>
        </div>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Составляющие</h2>
        <ul className="space-y-2">
          {components.map((c) => (
            <li key={c.key} className="grid grid-cols-[12rem_1fr_5rem] items-center gap-3 text-sm">
              <span>{c.title}</span>
              <div className="h-2.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuenow={c.score} aria-valuemax={c.max} aria-label={c.title}>
                {c.status === "checked" && (
                  <div
                    className={cn("h-full rounded-full", c.score >= c.max ? "bg-success" : c.score > 0 ? "bg-warning" : "bg-destructive")}
                    style={{ width: `${c.max > 0 ? Math.round((c.score / c.max) * 100) : 0}%` }}
                  />
                )}
              </div>
              <span className="text-right tabular-nums text-muted-foreground">
                {c.status === "checked" ? `${c.score} / ${c.max}` : c.status === "not_checked" ? "не проверено" : "выкл."}
              </span>
            </li>
          ))}
        </ul>
        {Object.values(evaluation.components).some((c) => c.status === "not_checked") && (
          <p className="text-xs text-muted-foreground">
            Составляющая «не проверено» не считается: итог пересчитан по остальным.
          </p>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Решение и цепочка статусов</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {reference && (
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-md bg-muted p-2">
                  <div className="text-xs text-muted-foreground">Эталон</div>
                  <div className="font-medium">{DECISION_TITLES[reference.decision]}</div>
                </div>
                <div className="rounded-md bg-muted p-2">
                  <div className="text-xs text-muted-foreground">Ваше решение</div>
                  <div className="font-medium">{decisionOf(evaluation)}</div>
                </div>
              </div>
            )}
            <ol className="space-y-1" aria-label="Ваши статусы против эталона">
              {referenceChain.map((step, i) => {
                const entry = dispatcherLog.find((e) => e.status === step.status);
                return (
                  <li key={i} className="flex items-start gap-2">
                    {entry ? <Check className="mt-0.5 size-4 text-success" aria-hidden /> : <X className="mt-0.5 size-4 text-destructive" aria-hidden />}
                    <div>
                      <span className="font-medium">{STATUS_TITLES[step.status] ?? step.status}</span>
                      {entry ? (
                        <span className="text-muted-foreground"> · {formatTime(entry.at)}{entry.order_number ? `, наряд ${entry.order_number}` : ""}</span>
                      ) : (
                        <span className="text-destructive"> · не проставлен</span>
                      )}
                      {step.comment_example && !entry?.comment && (
                        <div className="text-xs text-muted-foreground">Ожидался комментарий, например: «{step.comment_example}»</div>
                      )}
                    </div>
                  </li>
                );
              })}
              {extra.map((code, i) => (
                <li key={`extra-${i}`} className="flex items-start gap-2">
                  <CircleAlert className="mt-0.5 size-4 text-warning" aria-hidden />
                  <span>
                    <span className="font-medium">{STATUS_TITLES[code] ?? code}</span>
                    <span className="text-muted-foreground"> · лишний статус</span>
                  </span>
                </li>
              ))}
            </ol>
            {missing.length === 0 && extra.length === 0 && referenceChain.length > 0 && (
              <p className="text-xs text-success">Цепочка статусов совпала с эталоном.</p>
            )}
            <TimeNote attempt={attempt} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Типичные ошибки</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {evaluation.errors.length === 0 ? (
              <p className="text-success">Типичных ошибок из памятки не найдено.</p>
            ) : (
              <ul className="space-y-3">
                {evaluation.errors.map((e) => (
                  <li key={e.code} className="rounded-md border p-3" data-error={e.code}>
                    <div className="flex items-start justify-between gap-2">
                      <span className="font-medium">
                        {e.title}
                        {e.critical && <span className="ml-2 rounded bg-destructive/10 px-1.5 text-xs text-destructive">критичная</span>}
                      </span>
                      <span className="shrink-0 text-muted-foreground">−{e.penalty}</span>
                    </div>
                    <p className="mt-1 text-muted-foreground">{e.explanation}</p>
                    {e.memo_ref && (
                      <Link
                        to={`/student/reference?q=${encodeURIComponent(e.title)}`}
                        className="mt-1 inline-flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline"
                      >
                        <BookOpen className="size-3" aria-hidden /> Памятка, {e.memo_ref}
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Комментарии</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {evaluation.checked_text ? (
            <p className="whitespace-pre-line rounded-md bg-muted p-3 leading-relaxed">
              <Underlined text={evaluation.checked_text} items={grammar?.status === "checked" ? grammar.items : []} />
            </p>
          ) : (
            <p className="text-muted-foreground">Комментариев к статусам не было.</p>
          )}
          {grammar?.status === "not_checked" && (
            <p className="text-xs text-muted-foreground">Проверка грамотности недоступна: LanguageTool не ответил.</p>
          )}
          {grammar?.status === "checked" && grammar.items.length > 0 && (
            <ul className="text-xs text-muted-foreground">
              {grammar.items.map((it, i) => (
                <li key={i}>
                  {String(it.message ?? "ошибка")}
                  {Array.isArray(it.replacements) && it.replacements.length > 0 ? ` → ${(it.replacements as string[]).slice(0, 3).join(", ")}` : ""}
                </li>
              ))}
            </ul>
          )}
          {evaluation.ai_comment && <p className="rounded-md border p-3">{evaluation.ai_comment}</p>}
          <p className="text-xs text-muted-foreground">
            Методы: {Object.entries(evaluation.methods).map(([k, v]) => `${k}: ${v}`).join(", ") || "—"}
          </p>
        </CardContent>
      </Card>

      <div className="flex flex-wrap gap-2">
        <Button asChild>
          <Link to={`/student/sessions/${attempt.session.id}/journal`}>
            Следующая карточка <ArrowRight />
          </Link>
        </Button>
        <Button asChild variant="outline">
          <Link to={`/student/attempts/${attempt.id}`}>Открыть карточку</Link>
        </Button>
      </div>
    </div>
  );
}

function decisionOf(evaluation: Evaluation): string {
  const item = evaluation.components.decision?.items[0] as { actual?: string | null } | undefined;
  if (!item || !item.actual) return "не принято";
  return DECISION_TITLES[item.actual as "accept" | "reject"] ?? item.actual;
}

function TimeNote({ attempt }: { attempt: AttemptOut }) {
  if (!attempt.primary_status_at) return <p className="text-xs text-destructive">Первичный статус не проставлен.</p>;
  const seconds = (new Date(attempt.primary_status_at).getTime() - new Date(attempt.issued_at).getTime()) / 1000;
  const late = seconds > attempt.norm_seconds;
  return (
    <p className={cn("text-xs", late ? "text-destructive" : "text-muted-foreground")}>
      Первичный статус через {formatSeconds(seconds)} при нормативе {formatSeconds(attempt.norm_seconds)}
      {late ? " — позже норматива." : "."}
    </p>
  );
}

/** Text with grammar findings underlined (items carry offset/length into the text). */
function Underlined({ text, items }: { text: string; items: Record<string, unknown>[] }) {
  const spans = items
    .map((it) => ({ offset: Number(it.offset), length: Number(it.length), message: String(it.message ?? "") }))
    .filter((s) => Number.isFinite(s.offset) && s.length > 0)
    .sort((a, b) => a.offset - b.offset);
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  spans.forEach((s, i) => {
    if (s.offset < cursor) return;
    parts.push(<span key={`t${i}`}>{text.slice(cursor, s.offset)}</span>);
    parts.push(
      <mark key={`m${i}`} className="bg-transparent text-inherit underline decoration-destructive decoration-wavy underline-offset-2" title={s.message}>
        {text.slice(s.offset, s.offset + s.length)}
      </mark>,
    );
    cursor = s.offset + s.length;
  });
  parts.push(<span key="tail">{text.slice(cursor)}</span>);
  return <>{parts}</>;
}
