import { ArrowRight, BookOpen, Check, CircleAlert, X } from "lucide-react";
import { Link, Navigate, useParams } from "react-router-dom";

import { useAttempt, type AttemptOut } from "@/api/training";
import { useAuth } from "@/app/use-auth";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  acceptanceTimer,
  formatSeconds,
  formatTime,
  useNow,
} from "@/emulator/time";
import { describeCall, factTitle } from "@/emulator/service-call-model";
import {
  CallReview,
  TokenAudio,
  type CallEvaluation,
} from "@/intake/call-review";
import { cn } from "@/lib/utils";
import { ReviewNotes, ScoreBox } from "@/review/teacher-panel";

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
const COMPONENT_ORDER = [
  "decision",
  "time",
  "status_chain",
  "comments",
  "data_check",
  "service_call",
  "typical_errors",
  "grammar",
];
interface ServiceCallItem {
  service: string;
  called: boolean;
  seconds: number | null;
  norm_seconds: number;
  within_norm: boolean;
  required_facts: string[];
  facts_passed: string[];
  facts_missing: string[];
}
const VERDICT_TITLES: Record<string, string> = {
  found: "ошибка найдена и исправлена верно",
  wrong_correction: "ошибка найдена, но исправление неверно",
  missed: "ошибка не замечена",
  false_alarm: "отмечено верное поле",
};
interface DataCheckItem {
  field: string;
  title: string;
  wrong_value?: string;
  correct_value?: string;
  wrong_label?: string | null;
  correct_label?: string | null;
  corrected_value?: string | null;
  verdict: keyof typeof VERDICT_TITLES;
}

/** The review of an attempt: the trainee's own after the card is closed, or the teacher's
 * view of any attempt of their session (a card in work shows what is done so far). */
export function AttemptReviewPage() {
  const { attemptId } = useParams<{ attemptId: string }>();
  const { user } = useAuth();
  const teacher = user?.role === "teacher";
  if (!attemptId)
    return <Navigate to={teacher ? "/teacher" : "/student"} replace />;
  return <Review attemptId={attemptId} teacher={teacher} />;
}

function Review({
  attemptId,
  teacher,
}: {
  attemptId: string;
  teacher: boolean;
}) {
  const query = useAttempt(attemptId);
  if (query.isPending) return <LoadingState text="Готовим разбор…" />;
  if (query.isError)
    return (
      <ErrorState
        message={query.error.message}
        onRetry={() => void query.refetch()}
      />
    );
  const attempt = query.data;
  const evaluation = attempt.evaluation as Evaluation | null;
  const call = attempt.session.mode === "call_intake";
  if (!evaluation) {
    if (teacher && !call)
      return (
        <InProgressView
          attempt={attempt}
          onRefresh={() => void query.refetch()}
        />
      );
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold">
          Разбор {call ? "вызова" : "карточки"} {attempt.card.number}
        </h1>
        <p className="text-muted-foreground">
          {call
            ? teacher
              ? "Вызов ещё в работе: разбор появится, когда обучающийся сохранит карточку."
              : "Вызов ещё в работе: разбор появится, когда вы сохраните карточку."
            : "Карточка ещё в работе: разбор появится, когда вы поставите финальный статус или нажмёте «Завершить работу с карточкой»."}
        </p>
        {teacher ? (
          <Button variant="outline" onClick={() => void query.refetch()}>
            Обновить
          </Button>
        ) : (
          <Button asChild variant="outline">
            <Link to={`/student/attempts/${attempt.id}`}>
              Вернуться к {call ? "вызову" : "карточке"}
            </Link>
          </Button>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-6">
      {call ? (
        <CallReview
          attempt={attempt}
          evaluation={evaluation as unknown as CallEvaluation}
          teacher={teacher}
        />
      ) : (
        <ReviewView
          attempt={attempt}
          evaluation={evaluation}
          teacher={teacher}
        />
      )}
      <ReviewNotes
        attempt={attempt}
        teacher={teacher}
        total={evaluation.total}
      />
    </div>
  );
}

/** Teacher's look at a card still in work: the card, the statuses so far, the timer. */
function InProgressView({
  attempt,
  onRefresh,
}: {
  attempt: AttemptOut;
  onRefresh: () => void;
}) {
  const now = useNow();
  const timer = acceptanceTimer(
    attempt.issued_at,
    attempt.primary_status_at,
    attempt.norm_seconds,
    now,
  );
  const late = timer.phase === "overdue" || timer.phase === "late";
  return (
    <div className="space-y-6">
      <div>
        <Link
          to={`/teacher/sessions/${attempt.session.id}`}
          className="text-sm text-muted-foreground hover:underline"
        >
          ← К занятию
        </Link>
        <p className="mt-1 text-sm text-muted-foreground">
          Просмотр · карточка {attempt.card.number} · {attempt.arm.dispatcher}
        </p>
        <h1 className="text-2xl font-semibold">
          {attempt.card.incident.final_title || "Карточка"}
        </h1>
        <p className="text-sm text-muted-foreground">
          {attempt.card.address.text}
        </p>
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Что уже сделано</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p>
              Статус службы:{" "}
              <span className="font-medium">
                {attempt.response_status_title}
              </span>{" "}
              · карточка:{" "}
              <span
                className={cn(
                  "font-medium",
                  attempt.card_status_alert && "text-destructive",
                )}
              >
                {attempt.card_status_title}
              </span>
            </p>
            <p>
              {attempt.primary_status_at
                ? "Первичный статус через"
                : "Идёт норматив:"}{" "}
              <span className={cn("font-mono", late && "text-destructive")}>
                {formatSeconds(timer.elapsed)}
              </span>{" "}
              <span className="text-muted-foreground">
                при нормативе {attempt.norm_seconds} с
              </span>
            </p>
            <ol className="space-y-1" aria-label="Статусы">
              {attempt.status_log.map((e, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="w-16 shrink-0 font-mono text-xs text-muted-foreground">
                    {formatTime(e.at)}
                  </span>
                  <span>
                    {e.title}
                    {e.order_number ? `, наряд ${e.order_number}` : ""}
                    {e.comment ? ` — ${e.comment}` : ""}
                    {e.by === "system" && (
                      <span className="text-muted-foreground"> (система)</span>
                    )}
                  </span>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Карточка</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              <span className="text-muted-foreground">Заявитель:</span>{" "}
              {attempt.card.caller.name || "—"}
              {attempt.card.caller.role && ` (${attempt.card.caller.role})`}
            </p>
            <p>
              <span className="text-muted-foreground">Описание:</span>{" "}
              {attempt.card.description || "—"}
            </p>
            <p>
              <span className="text-muted-foreground">Признаки:</span>{" "}
              {attempt.card.incident.signs.join(", ") || "—"}
            </p>
          </CardContent>
        </Card>
      </div>
      <p className="text-xs text-muted-foreground">
        Разбор с оценкой появится, когда обучающийся закроет карточку.
      </p>
      <Button variant="outline" onClick={onRefresh}>
        Обновить
      </Button>
    </div>
  );
}

function ReviewView({
  attempt,
  evaluation,
  teacher,
}: {
  attempt: AttemptOut;
  evaluation: Evaluation;
  teacher: boolean;
}) {
  const reference = attempt.reference as Reference | null;
  const components = COMPONENT_ORDER.map(
    (k) => evaluation.components[k],
  ).filter((c): c is Component => Boolean(c));
  const dispatcherLog = attempt.status_log.filter((e) => e.by !== "system");
  const referenceChain = reference?.status_chain ?? [];
  const actualCodes = dispatcherLog.map((e) => e.status);
  const missing = referenceChain
    .filter((s) => !actualCodes.includes(s.status))
    .map((s) => s.status);
  const extra = actualCodes.filter(
    (c) => !referenceChain.some((s) => s.status === c),
  );
  const grammar = evaluation.components.grammar;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          {teacher && (
            <Link
              to={`/teacher/sessions/${attempt.session.id}/report`}
              className="text-sm text-muted-foreground hover:underline"
            >
              ← К отчёту
            </Link>
          )}
          <p className="text-sm text-muted-foreground">
            Разбор · карточка {attempt.card.number}
            {teacher && ` · ${attempt.arm.dispatcher}`}
          </p>
          <h1 className="text-2xl font-semibold">
            {attempt.card.incident.final_title || "Карточка"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {attempt.card.address.text}
          </p>
        </div>
        <ScoreBox
          total={evaluation.total}
          passed={evaluation.passed}
          override={attempt.override}
          reasons={evaluation.errors.filter((e) => e.critical).map((e) => e.explanation)}
        />
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Составляющие</h2>
        <ul className="space-y-2">
          {components.map((c) => (
            <li
              key={c.key}
              className="grid grid-cols-[12rem_1fr_5rem] items-center gap-3 text-sm"
            >
              <span>{c.title}</span>
              <div
                className="h-2.5 overflow-hidden rounded-full bg-muted"
                role="progressbar"
                aria-valuenow={c.score}
                aria-valuemax={c.max}
                aria-label={c.title}
              >
                {c.status === "checked" && (
                  <div
                    className={cn(
                      "h-full rounded-full",
                      c.score >= c.max
                        ? "bg-success"
                        : c.score > 0
                          ? "bg-warning"
                          : "bg-destructive",
                    )}
                    style={{
                      width: `${c.max > 0 ? Math.round((c.score / c.max) * 100) : 0}%`,
                    }}
                  />
                )}
              </div>
              <span className="text-right tabular-nums text-muted-foreground">
                {c.status === "checked"
                  ? `${c.score} / ${c.max}`
                  : c.status === "not_checked"
                    ? "не проверено"
                    : "выкл."}
              </span>
            </li>
          ))}
        </ul>
        {Object.values(evaluation.components).some(
          (c) => c.status === "not_checked",
        ) && (
          <p className="text-xs text-muted-foreground">
            Составляющая «не проверено» не считается: итог пересчитан по
            остальным.
          </p>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Решение и цепочка статусов
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {reference && (
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-md bg-muted p-2">
                  <div className="text-xs text-muted-foreground">Эталон</div>
                  <div className="font-medium">
                    {DECISION_TITLES[reference.decision]}
                  </div>
                </div>
                <div className="rounded-md bg-muted p-2">
                  <div className="text-xs text-muted-foreground">
                    {teacher ? "Решение обучающегося" : "Ваше решение"}
                  </div>
                  <div className="font-medium">{decisionOf(evaluation)}</div>
                </div>
              </div>
            )}
            <ol
              className="space-y-1"
              aria-label={
                teacher
                  ? "Статусы обучающегося против эталона"
                  : "Ваши статусы против эталона"
              }
            >
              {referenceChain.map((step, i) => {
                const entry = dispatcherLog.find(
                  (e) => e.status === step.status,
                );
                return (
                  <li key={i} className="flex items-start gap-2">
                    {entry ? (
                      <Check
                        className="mt-0.5 size-4 text-success"
                        aria-hidden
                      />
                    ) : (
                      <X
                        className="mt-0.5 size-4 text-destructive"
                        aria-hidden
                      />
                    )}
                    <div>
                      <span className="font-medium">
                        {STATUS_TITLES[step.status] ?? step.status}
                      </span>
                      {entry ? (
                        <span className="text-muted-foreground">
                          {" "}
                          · {formatTime(entry.at)}
                          {entry.order_number
                            ? `, наряд ${entry.order_number}`
                            : ""}
                        </span>
                      ) : (
                        <span className="text-destructive">
                          {" "}
                          · не проставлен
                        </span>
                      )}
                      {step.comment_example && !entry?.comment && (
                        <div className="text-xs text-muted-foreground">
                          Ожидался комментарий, например: «
                          {step.comment_example}»
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
              {extra.map((code, i) => (
                <li key={`extra-${i}`} className="flex items-start gap-2">
                  <CircleAlert
                    className="mt-0.5 size-4 text-warning"
                    aria-hidden
                  />
                  <span>
                    <span className="font-medium">
                      {STATUS_TITLES[code] ?? code}
                    </span>
                    <span className="text-muted-foreground">
                      {" "}
                      · лишний статус
                    </span>
                  </span>
                </li>
              ))}
            </ol>
            {missing.length === 0 &&
              extra.length === 0 &&
              referenceChain.length > 0 && (
                <p className="text-xs text-success">
                  Цепочка статусов совпала с эталоном.
                </p>
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
              <p className="text-success">
                Типичных ошибок из памятки не найдено.
              </p>
            ) : (
              <ul className="space-y-3">
                {evaluation.errors.map((e) => (
                  <li
                    key={e.code}
                    className="rounded-md border p-3"
                    data-error={e.code}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="font-medium">
                        {e.title}
                        {e.critical && (
                          <span className="ml-2 rounded bg-destructive/10 px-1.5 text-xs text-destructive">
                            критичная
                          </span>
                        )}
                      </span>
                      <span className="shrink-0 text-muted-foreground">
                        −{e.penalty}
                      </span>
                    </div>
                    <p className="mt-1 text-muted-foreground">
                      {e.explanation}
                    </p>
                    {e.memo_ref && (
                      <Link
                        to={`/student/reference?q=${encodeURIComponent(e.title)}`}
                        className="mt-1 inline-flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline"
                      >
                        <BookOpen className="size-3" aria-hidden /> Памятка,{" "}
                        {e.memo_ref}
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      {evaluation.components.data_check && (
        <DataCheckCard
          component={evaluation.components.data_check}
          teacher={teacher}
        />
      )}
      {(evaluation.components.service_call ||
        attempt.service_calls.length > 0) && (
        <ServiceCallsCard
          attempt={attempt}
          component={evaluation.components.service_call}
          teacher={teacher}
        />
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Комментарии</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {evaluation.checked_text ? (
            <p className="whitespace-pre-line rounded-md bg-muted p-3 leading-relaxed">
              <Underlined
                text={evaluation.checked_text}
                items={grammar?.status === "checked" ? grammar.items : []}
              />
            </p>
          ) : (
            <p className="text-muted-foreground">
              Комментариев к статусам не было.
            </p>
          )}
          {grammar?.status === "not_checked" && (
            <p className="text-xs text-muted-foreground">
              Проверка грамотности недоступна: LanguageTool не ответил.
            </p>
          )}
          {grammar?.status === "checked" && grammar.items.length > 0 && (
            <ul className="text-xs text-muted-foreground">
              {grammar.items.map((it, i) => (
                <li key={i}>
                  {String(it.message ?? "ошибка")}
                  {Array.isArray(it.replacements) && it.replacements.length > 0
                    ? ` → ${(it.replacements as string[]).slice(0, 3).join(", ")}`
                    : ""}
                </li>
              ))}
            </ul>
          )}
          {evaluation.ai_comment && (
            <p className="rounded-md border p-3">{evaluation.ai_comment}</p>
          )}
          <p className="text-xs text-muted-foreground">
            Методы:{" "}
            {Object.entries(evaluation.methods)
              .map(([k, v]) => `${k}: ${v}`)
              .join(", ") || "—"}
          </p>
        </CardContent>
      </Card>

      {teacher ? (
        <div className="flex flex-wrap gap-2">
          <Button asChild>
            <Link to={`/teacher/sessions/${attempt.session.id}`}>
              К занятию
            </Link>
          </Button>
          <Button asChild variant="outline">
            <Link to={`/teacher/sessions/${attempt.session.id}/report`}>
              К отчёту
            </Link>
          </Button>
        </div>
      ) : (
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
      )}
    </div>
  );
}

/** Issue #35: what the 112 operator got wrong in the card and how the dispatcher reacted. */
function DataCheckCard({
  component,
  teacher,
}: {
  component: Component;
  teacher: boolean;
}) {
  const items = component.items as unknown as DataCheckItem[];
  const shown = (
    value: string | null | undefined,
    label: string | null | undefined,
  ) => label ?? value ?? "—";
  return (
    <Card data-testid="data-check">
      <CardHeader>
        <CardTitle className="text-base">Проверка данных</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          Карточка пришла от оператора 112 с ошибками.{" "}
          {teacher ? "Обучающийся" : "Вы"} отметил{teacher ? "" : "и"}:{" "}
          {items.filter((i) => i.verdict !== "missed").length} из{" "}
          {items.filter((i) => i.verdict !== "false_alarm").length}, лишних
          отметок: {items.filter((i) => i.verdict === "false_alarm").length}.
        </p>
        <ul className="space-y-2" aria-label="Ошибки в данных карточки">
          {items.map((item, i) => (
            <li
              key={`${item.field}-${i}`}
              className="flex items-start gap-2"
              data-verdict={item.verdict}
            >
              {item.verdict === "found" ? (
                <Check className="mt-0.5 size-4 text-success" aria-hidden />
              ) : item.verdict === "false_alarm" ? (
                <CircleAlert
                  className="mt-0.5 size-4 text-warning"
                  aria-hidden
                />
              ) : (
                <X className="mt-0.5 size-4 text-destructive" aria-hidden />
              )}
              <div>
                <span className="font-medium">{item.title}</span>
                <span className="text-muted-foreground">
                  {" "}
                  · {VERDICT_TITLES[item.verdict] ?? item.verdict}
                </span>
                {item.verdict !== "false_alarm" && (
                  <div className="text-xs text-muted-foreground">
                    В карточке: «{shown(item.wrong_value, item.wrong_label)}»,
                    верно: «{shown(item.correct_value, item.correct_label)}»
                    {item.corrected_value
                      ? `, ${teacher ? "обучающийся указал" : "вы указали"}: «${item.corrected_value}»`
                      : ""}
                  </div>
                )}
                {item.verdict === "false_alarm" && item.corrected_value && (
                  <div className="text-xs text-muted-foreground">
                    Поле было верным;{" "}
                    {teacher ? "обучающийся указал" : "вы указали"}: «
                    {item.corrected_value}»
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

/** Issue #36: whom the dispatcher called, how long, which facts were passed, the recording. */
function ServiceCallsCard({
  attempt,
  component,
  teacher,
}: {
  attempt: AttemptOut;
  component: Component | undefined;
  teacher: boolean;
}) {
  const items = (component?.items ?? []) as unknown as ServiceCallItem[];
  const who = teacher ? "обучающийся" : "вы";
  return (
    <Card data-testid="service-calls">
      <CardHeader>
        <CardTitle className="text-base">Звонки в службы</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {items.length > 0 && (
          <ul className="space-y-2" aria-label="Требуемые звонки">
            {items.map((item) => {
              const call = attempt.service_calls.find(
                (c) => c.service === item.service,
              );
              return (
                <li
                  key={item.service}
                  className="flex items-start gap-2"
                  data-service={item.service}
                  data-called={item.called}
                >
                  {item.called && item.facts_missing.length === 0 ? (
                    <Check className="mt-0.5 size-4 text-success" aria-hidden />
                  ) : item.called ? (
                    <CircleAlert
                      className="mt-0.5 size-4 text-warning"
                      aria-hidden
                    />
                  ) : (
                    <X className="mt-0.5 size-4 text-destructive" aria-hidden />
                  )}
                  <div>
                    <span className="font-medium">
                      {call?.service_title ?? item.service}
                    </span>
                    <span className="text-muted-foreground">
                      {" · "}
                      {item.called
                        ? `звонок ${item.seconds != null ? formatSeconds(item.seconds) : "—"} при нормативе ${formatSeconds(item.norm_seconds)}${item.within_norm ? "" : ", дольше норматива"}`
                        : `${who === "вы" ? "вы не позвонили" : "обучающийся не позвонил"}`}
                    </span>
                    <div className="text-xs text-muted-foreground">
                      Передано:{" "}
                      {item.facts_passed.map(factTitle).join(", ") || "ничего"}
                      {item.facts_missing.length > 0 && (
                        <span className="text-destructive">
                          {" "}
                          · не передано:{" "}
                          {item.facts_missing.map(factTitle).join(", ")}
                        </span>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {attempt.service_calls.map((call) => (
          <details
            key={call.id}
            className="rounded-md border p-3"
            data-testid="service-call-transcript"
          >
            <summary className="cursor-pointer text-sm">
              {call.service_title} · {formatTime(call.started_at)}
              {call.seconds != null
                ? ` · ${formatSeconds(call.seconds)}`
                : " · не завершён"}{" "}
              · {describeCall(call)}
            </summary>
            <ol
              className="mt-2 space-y-1 text-sm"
              aria-label="Стенограмма звонка"
            >
              {call.turns.map((t) => (
                <li
                  key={t.index}
                  className={cn(
                    "rounded-md px-2 py-1",
                    t.role === "caller" ? "bg-muted" : "",
                  )}
                >
                  <span className="text-xs text-muted-foreground">
                    {t.role === "caller" ? "дежурный" : who}:{" "}
                  </span>
                  {t.text}
                </li>
              ))}
            </ol>
            {call.recording_available && (
              <TokenAudio
                url={`/api/attempts/${attempt.id}/service-call/${call.id}/recording`}
                label="Запись разговора со службой"
                className="mt-2 w-full"
              />
            )}
          </details>
        ))}
        {items.length === 0 && attempt.service_calls.length === 0 && (
          <p className="text-muted-foreground">Звонков в службы не было.</p>
        )}
      </CardContent>
    </Card>
  );
}

function decisionOf(evaluation: Evaluation): string {
  const item = evaluation.components.decision?.items[0] as
    { actual?: string | null } | undefined;
  if (!item || !item.actual) return "не принято";
  return DECISION_TITLES[item.actual as "accept" | "reject"] ?? item.actual;
}

function TimeNote({ attempt }: { attempt: AttemptOut }) {
  if (!attempt.primary_status_at)
    return (
      <p className="text-xs text-destructive">
        Первичный статус не проставлен.
      </p>
    );
  const seconds =
    (new Date(attempt.primary_status_at).getTime() -
      new Date(attempt.issued_at).getTime()) /
    1000;
  const late = seconds > attempt.norm_seconds;
  return (
    <p
      className={cn(
        "text-xs",
        late ? "text-destructive" : "text-muted-foreground",
      )}
    >
      Первичный статус через {formatSeconds(seconds)} при нормативе{" "}
      {formatSeconds(attempt.norm_seconds)}
      {late ? " — позже норматива." : "."}
    </p>
  );
}

/** Text with grammar findings underlined (items carry offset/length into the text). */
function Underlined({
  text,
  items,
}: {
  text: string;
  items: Record<string, unknown>[];
}) {
  const spans = items
    .map((it) => ({
      offset: Number(it.offset),
      length: Number(it.length),
      message: String(it.message ?? ""),
    }))
    .filter((s) => Number.isFinite(s.offset) && s.length > 0)
    .sort((a, b) => a.offset - b.offset);
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  spans.forEach((s, i) => {
    if (s.offset < cursor) return;
    parts.push(<span key={`t${i}`}>{text.slice(cursor, s.offset)}</span>);
    parts.push(
      <mark
        key={`m${i}`}
        className="bg-transparent text-inherit underline decoration-destructive decoration-wavy underline-offset-2"
        title={s.message}
      >
        {text.slice(s.offset, s.offset + s.length)}
      </mark>,
    );
    cursor = s.offset + s.length;
  });
  parts.push(<span key="tail">{text.slice(cursor)}</span>);
  return <>{parts}</>;
}
