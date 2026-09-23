import {
  Check,
  CheckCheck,
  History,
  Loader2,
  MessageSquare,
  Mic,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  SpellCheck,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  useAddReply,
  useApproveReplies,
  useApproveScenario,
  useDeleteReply,
  useEditReply,
  useGrammarCheck,
  useJob,
  usePreviewDialog,
  useRemoveScenario,
  useRestoreScenario,
  useReviseScenario,
  useScenario,
  useScenarioOptions,
  useUpdateScenario,
  useUploadReplyAudio,
  type GrammarIssueOut,
  type PreviewTurnIn,
  type ReplyOut,
  type ScenarioOut,
} from "@/api/scenarios";
import { getAccessToken } from "@/api/token";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { factTitle } from "@/emulator/service-call-model";
import { formatDateTime } from "@/emulator/time";
import { MODE_TITLES, RESPONSE_STATUS_TITLES } from "@/teacher/labels";
import {
  DECISION_TITLES,
  DIFFICULTY_SHORT,
  FACT_TITLES,
  INCIDENT_FLAG_TITLES,
  SCENARIO_STATUS_TITLES,
  SCENARIO_STATUS_TONES,
  SOURCE_TITLES,
  VOICING_TITLES,
  formatAddress,
} from "@/teacher/scenario-labels";
import { JobProgress } from "@/pages/teacher/scenarios";

const selectClass =
  "flex h-9 w-full rounded-md border border-input bg-background text-foreground px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50";
const textareaClass =
  "flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50";

type Body = Record<string, unknown>;
const obj = (v: unknown): Body =>
  v && typeof v === "object" ? (v as Body) : {};
const str = (v: unknown): string =>
  v === null || v === undefined ? "" : String(v);

/** Scenario card: facts, reference, replies with player and approval, revision, preview. */
export function TeacherScenarioPage() {
  const { scenarioId = "" } = useParams<{ scenarioId: string }>();
  const [voicingPoll, setVoicingPoll] = useState(false);
  const query = useScenario(scenarioId, voicingPoll ? 2000 : false);

  useEffect(() => {
    const queued =
      query.data?.replies.some((r) => r.voicing === "queued") ?? false;
    setVoicingPoll(queued);
  }, [query.data]);

  if (query.isPending) return <LoadingState text="Загружаем сценарий…" />;
  if (query.isError)
    return (
      <ErrorState
        message={query.error.message}
        onRetry={() => void query.refetch()}
      />
    );
  const scenario = query.data;

  return (
    <div className="space-y-6">
      <Link
        to="/teacher/scenarios"
        className="text-sm text-muted-foreground hover:underline"
      >
        ← Сценарии
      </Link>
      <Header scenario={scenario} />
      {scenario.problems.length > 0 && (
        <div
          className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm"
          role="alert"
        >
          <p className="font-medium">
            Сценарий не пройдёт утверждение, пока не исправлено:
          </p>
          <ul className="ml-5 list-disc">
            {scenario.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      {/* An archived scenario is read-only: every editor below is inert until it is restored. */}
      <div
        className="grid gap-6 lg:grid-cols-2"
        inert={scenario.status === "archived" || undefined}
      >
        <div className="space-y-6">
          {scenario.kind === "call_intake" ? (
            <CallerFacts scenario={scenario} />
          ) : (
            <CardFacts scenario={scenario} />
          )}
          <Reference scenario={scenario} />
        </div>
        <div className="space-y-6">
          {scenario.kind === "call_intake" && <Replies scenario={scenario} />}
          {scenario.kind === "call_intake" && (
            <PreviewDialog scenario={scenario} />
          )}
          <Revise scenario={scenario} />
          <Versions scenario={scenario} />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- header, approval, grammar

function Header({ scenario }: { scenario: ScenarioOut }) {
  const navigate = useNavigate();
  const approve = useApproveScenario(scenario.id);
  const grammar = useGrammarCheck(scenario.id);
  const remove = useRemoveScenario(scenario.id);
  const restore = useRestoreScenario(scenario.id);
  const options = useScenarioOptions();
  const [issues, setIssues] = useState<GrammarIssueOut[] | null>(null);
  const [needConfirm, setNeedConfirm] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const archived = scenario.status === "archived";

  const doRemove = () =>
    remove.mutate(undefined, {
      onSuccess: (data) => {
        setConfirmRemove(false);
        if (data.result === "deleted") void navigate("/teacher/scenarios");
      },
    });

  const runGrammar = async () => {
    const report = await grammar.mutateAsync();
    setIssues(report.issues);
    return report;
  };

  const doApprove = async (confirm: boolean) => {
    setNeedConfirm(false);
    try {
      await approve.mutateAsync({
        reference: true,
        replies: true,
        confirm_grammar: confirm,
      });
    } catch (error) {
      if (error instanceof Error && error.message.includes("грамотност")) {
        setNeedConfirm(true);
        await runGrammar();
      }
    }
  };

  const generation = scenario.generation as {
    method?: string;
    note?: string | null;
    phrase?: string | null;
  } | null;
  const student = scenario.body.student as
    { full_name?: string; login?: string } | null | undefined;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{scenario.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {MODE_TITLES[scenario.kind]} ·{" "}
            {DIFFICULTY_SHORT[scenario.difficulty]} ·{" "}
            {SOURCE_TITLES[scenario.source] ?? scenario.source}
            {scenario.ticket_ref ? ` · билет ${scenario.ticket_ref}` : ""} ·
            версия {scenario.current_version}
            {scenario.author ? ` · ${scenario.author}` : ""}
          </p>
          {student && (
            <p className="text-xs text-muted-foreground">
              Карточка обучающегося: {student.full_name ?? student.login} —
              сохранена в приёме вызова, после утверждения пойдёт в занятия с
              источником «карточки обучающихся».
            </p>
          )}
          {generation?.method && generation.method !== "student" && (
            <p className="text-xs text-muted-foreground">
              Сгенерирован:{" "}
              {generation.method === "llm" ? "языковая модель" : "шаблон"}
              {generation.phrase ? ` по фразе «${generation.phrase}»` : ""}
              {generation.note ? `. ${generation.note}` : ""}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            tone={SCENARIO_STATUS_TONES[scenario.status]}
            className="text-sm"
          >
            {SCENARIO_STATUS_TITLES[scenario.status]}
          </Badge>
          <Button
            variant="outline"
            onClick={() => void runGrammar()}
            disabled={grammar.isPending}
          >
            {grammar.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <SpellCheck />
            )}{" "}
            Проверить грамотность
          </Button>
          {archived ? (
            <Button
              variant="outline"
              onClick={() => restore.mutate()}
              disabled={restore.isPending}
            >
              {restore.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <RotateCcw />
              )}{" "}
              Восстановить
            </Button>
          ) : (
            <Button
              variant="outline"
              className="text-destructive"
              onClick={() => setConfirmRemove(true)}
              disabled={remove.isPending || confirmRemove}
            >
              <Trash2 /> Удалить
            </Button>
          )}
          {!scenario.fully_approved && !archived && (
            <Button
              onClick={() => void doApprove(false)}
              disabled={approve.isPending || scenario.problems.length > 0}
            >
              {approve.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <CheckCheck />
              )}{" "}
              Утвердить целиком
            </Button>
          )}
        </div>
      </div>
      {archived && (
        <p className="text-sm text-muted-foreground" role="status">
          Сценарий в архиве: он не предлагается занятиям и не редактируется. Разборы прошлых
          занятий по нему открываются как раньше.
        </p>
      )}
      {confirmRemove && (
        <div
          className="flex flex-wrap items-center gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm"
          role="alertdialog"
          aria-label="Удаление сценария"
        >
          <span>
            Удалить сценарий «{scenario.title}»? Если по нему уже занимались или он из набора
            заказчика, он уйдёт в архив, чтобы разборы занятий остались.
          </span>
          <Button
            size="sm"
            variant="destructive"
            onClick={doRemove}
            disabled={remove.isPending}
          >
            {remove.isPending ? "Удаляем…" : "Да, удалить"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirmRemove(false)}>
            Отмена
          </Button>
        </div>
      )}
      {(remove.isError || restore.isError) && (
        <p className="text-sm text-destructive" role="alert">
          {remove.error?.message ?? restore.error?.message}
        </p>
      )}
      {approve.isError && !needConfirm && (
        <p className="text-sm text-destructive" role="alert">
          {approve.error.message}
        </p>
      )}
      {needConfirm && (
        <div
          className="flex flex-wrap items-center gap-3 rounded-lg border border-warning/60 bg-warning/10 p-3 text-sm"
          role="alert"
        >
          <span>
            Проверка грамотности нашла замечания (ниже). Утвердить, несмотря на
            них?
          </span>
          <Button size="sm" onClick={() => void doApprove(true)}>
            Утвердить всё равно
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setNeedConfirm(false)}
          >
            Отмена
          </Button>
        </div>
      )}
      {issues !== null && (
        <GrammarIssues
          issues={issues}
          available={options.data?.grammar_available ?? true}
          onClose={() => setIssues(null)}
        />
      )}
    </div>
  );
}

function GrammarIssues({
  issues,
  available,
  onClose,
}: {
  issues: GrammarIssueOut[];
  available: boolean;
  onClose: () => void;
}) {
  return (
    <Card data-testid="grammar-report">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">Грамотность</CardTitle>
        <Button
          variant="ghost"
          size="sm"
          onClick={onClose}
          aria-label="Скрыть проверку грамотности"
        >
          <X />
        </Button>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {!available && (
          <p className="text-muted-foreground">
            Сервис проверки грамотности не запущен: текст не проверялся.
          </p>
        )}
        {available && issues.length === 0 && (
          <p className="text-success">Замечаний нет.</p>
        )}
        {issues.map((issue, index) => (
          <div key={`${issue.field}-${index}`} className="rounded border p-2">
            <p className="text-xs text-muted-foreground">
              {fieldTitle(issue.field)}
            </p>
            <p>
              {issue.text.slice(0, issue.offset)}
              <mark className="rounded bg-destructive/20 underline decoration-destructive decoration-wavy">
                {issue.text.slice(issue.offset, issue.offset + issue.length)}
              </mark>
              {issue.text.slice(issue.offset + issue.length)}
            </p>
            <p className="text-xs">
              {issue.message}
              {issue.replacements.length > 0 && (
                <> — варианты: {issue.replacements.join(", ")}</>
              )}
            </p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function fieldTitle(field: string): string {
  if (field === "description") return "Описание в эталоне";
  if (field.startsWith("reply:")) return `Реплика ${field.slice(6)}`;
  if (field.startsWith("comment:")) return "Комментарий в эталоне";
  return field;
}

// ---------------------------------------------------------------- facts

function CallerFacts({ scenario }: { scenario: ScenarioOut }) {
  const caller = obj(scenario.body.caller);
  const facts = obj(caller.facts);
  const options = useScenarioOptions();
  const persona = options.data?.personas.find((p) => p.code === caller.persona);
  const noise = options.data?.noises.find((n) => n.code === caller.noise);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Лист фактов заявителя</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>
          <span className="text-muted-foreground">Персонаж:</span>{" "}
          {persona?.title ?? str(caller.persona)}
          {persona ? ` — ${persona.style}` : ""}
          {noise ? ` · фон: ${noise.title.toLowerCase()}` : ""}
          {caller.drops_call ? " · бросает трубку" : ""}
        </p>
        <p>
          <span className="text-muted-foreground">Первая фраза:</span> «
          {str(caller.opening)}»
        </p>
        <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[max-content_1fr]">
          {Object.entries(facts).map(([key, value]) => (
            <FactRow
              key={key}
              title={FACT_TITLES[key] ?? key}
              value={str(value)}
            />
          ))}
        </dl>
        {caller.behaviour ? (
          <p>
            <span className="text-muted-foreground">Поведение:</span>{" "}
            {str(caller.behaviour)}
          </p>
        ) : null}
        <p className="text-muted-foreground">
          Обязательно выяснить:{" "}
          {(scenario.body.required_topics as string[] | undefined)
            ?.map(
              (code) =>
                options.data?.topics.find((t) => t.code === code)?.title ??
                code,
            )
            .join(", ")}
        </p>
      </CardContent>
    </Card>
  );
}

function FactRow({ title, value }: { title: string; value: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{title}</dt>
      <dd>{value || "—"}</dd>
    </>
  );
}

function CardFacts({ scenario }: { scenario: ScenarioOut }) {
  const card = obj(scenario.body.card);
  const caller = obj(card.caller);
  const flags = obj(card.flags);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          Карточка, как её видит диспетчер
        </CardTitle>
      </CardHeader>
      <CardContent className="text-sm">
        <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[max-content_1fr]">
          <FactRow title="Номер" value={str(card.number)} />
          <FactRow
            title="Тип"
            value={`${scenario.incident_type_title ?? ""} (${str(card.incident_type)})`}
          />
          <FactRow
            title="Признаки"
            value={((card.signs as string[]) ?? []).join(" → ")}
          />
          <FactRow
            title="Флаги"
            value={
              Object.entries(flags)
                .filter(([, v]) => v)
                .map(([k]) => INCIDENT_FLAG_TITLES[k] ?? k)
                .join(", ") || "нет"
            }
          />
          <FactRow title="Адрес" value={formatAddress(obj(card.address))} />
          <FactRow
            title="Заявитель"
            value={[caller.name, caller.role, caller.phone]
              .filter(Boolean)
              .map(str)
              .join(", ")}
          />
          <FactRow title="Описание" value={str(card.description)} />
          <FactRow
            title="Служба обучающегося"
            value={scenario.service_code ?? ""}
          />
        </dl>
        <p className="mt-3 text-muted-foreground">
          Оповещены: {scenario.services.map((s) => s.title).join(", ") || "—"}
        </p>
        <PlantedErrors body={scenario.body} />
      </CardContent>
    </Card>
  );
}

/** Operator mistakes planted in the card (issue #35); the trainee never sees this list. */
function PlantedErrors({ body }: { body: Body }) {
  const errors = (body.injected_errors as Body[] | undefined) ?? [];
  if (errors.length === 0) return null;
  return (
    <div className="mt-3" data-testid="planted-errors">
      <p className="font-medium">
        Заложенные ошибки оператора 112 ({errors.length})
      </p>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
        {errors.map((e, i) => (
          <li key={i}>
            {str(e.field)}: в карточке «{str(e.wrong_label || e.wrong_value)}»,
            верно «{str(e.correct_label || e.correct_value)}»
            {e.hint_level ? ` · заметность ${str(e.hint_level)}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- reference

function Reference({ scenario }: { scenario: ScenarioOut }) {
  const update = useUpdateScenario(scenario.id);
  const approve = useApproveScenario(scenario.id);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const isCall = scenario.kind === "call_intake";
  const reference = isCall
    ? obj(scenario.body.reference_card)
    : obj(scenario.body.reference);
  const card = isCall ? reference : obj(scenario.body.card);
  const flags = obj(card.flags);
  const description = isCall
    ? str(reference.description)
    : str(card.description);

  const startEdit = () => {
    setText(description);
    setEditing(true);
  };
  const save = async () => {
    const body = structuredClone(scenario.body) as Body;
    if (isCall) (body.reference_card as Body).description = text;
    else (body.card as Body).description = text;
    await update.mutateAsync(body);
    setEditing(false);
  };

  return (
    <Card data-testid="reference">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">Эталон</CardTitle>
        <div className="flex items-center gap-2">
          {scenario.reference_approved ? (
            <Badge tone="success">утверждён</Badge>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                approve.mutate({
                  reference: true,
                  replies: false,
                  confirm_grammar: true,
                })
              }
              disabled={approve.isPending || scenario.problems.length > 0}
            >
              <Check /> Утвердить эталон
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {isCall ? (
          <>
            <p>
              <span className="text-muted-foreground">Путь признаков:</span>{" "}
              {((reference.signs_path as string[]) ?? []).map((s, i) => (
                <span key={s}>
                  {i > 0 && " → "}
                  <Badge tone="primary">{s}</Badge>
                </span>
              ))}
              <span className="ml-2 text-muted-foreground">
                {scenario.incident_type_title} ({str(reference.incident_type)})
              </span>
            </p>
            <p>
              <span className="text-muted-foreground">Флаги:</span>{" "}
              {Object.entries(flags).map(([k, v]) => (
                <Badge
                  key={k}
                  tone={v ? "warning" : "neutral"}
                  className="mr-1"
                >
                  {INCIDENT_FLAG_TITLES[k] ?? k}: {v ? "да" : "нет"}
                </Badge>
              ))}
            </p>
            <p>
              <span className="text-muted-foreground">
                Службы (подставляются по признакам):
              </span>{" "}
              {scenario.services.map((s) => (
                <Badge key={s.code} tone="warning" className="mr-1">
                  {s.title}
                </Badge>
              ))}
            </p>
            <p>
              <span className="text-muted-foreground">Адрес:</span>{" "}
              {formatAddress(obj(reference.address))}
            </p>
            <p>
              <span className="text-muted-foreground">
                Ключевые слова описания:
              </span>{" "}
              {((reference.description_keywords as string[]) ?? []).join(
                ", ",
              ) || "—"}
            </p>
          </>
        ) : (
          <>
            <p>
              <span className="text-muted-foreground">Решение:</span>{" "}
              <Badge
                tone={reference.decision === "accept" ? "success" : "danger"}
              >
                {DECISION_TITLES[str(reference.decision)] ??
                  str(reference.decision)}
              </Badge>
              {reference.reject_reason ? (
                <span className="ml-2 text-muted-foreground">
                  причина: {str(reference.reject_reason)}
                </span>
              ) : null}
            </p>
            <ol className="ml-5 list-decimal space-y-1">
              {((reference.status_chain as Body[]) ?? []).map((step, i) => (
                <li key={i}>
                  {RESPONSE_STATUS_TITLES[str(step.status)] ?? str(step.status)}
                  {step.order_number ? " (с номером наряда)" : ""}
                  {step.comment_example ? (
                    <span className="text-muted-foreground">
                      {" "}
                      — «{str(step.comment_example)}»
                    </span>
                  ) : null}
                </li>
              ))}
            </ol>
            <p className="text-muted-foreground">
              Критичные ошибки:{" "}
              {((reference.critical_errors as string[]) ?? []).join(", ") ||
                "—"}
            </p>
            <p
              className="text-muted-foreground"
              data-testid="reference-service-calls"
            >
              Звонки в службы:{" "}
              {((reference.service_calls as Body[]) ?? []).length === 0
                ? "не требуются"
                : ((reference.service_calls as Body[]) ?? [])
                    .map(
                      (c) =>
                        `${scenario.services.find((s) => s.code === str(c.service))?.title ?? str(c.service)} (${((c.required_facts as string[]) ?? []).map(factTitle).join(", ")}, норматив ${str(c.norm_seconds ?? 120)} с)`,
                    )
                    .join("; ")}
            </p>
            <p
              className="text-muted-foreground"
              data-testid="reference-reports"
            >
              Доклады бригады по телефону:{" "}
              {((reference.reports as Body[]) ?? []).length === 0
                ? "нет (статусы хода работ ученик ставит сам)"
                : ((reference.reports as Body[]) ?? [])
                    .map(
                      (r) =>
                        `через ${str(r.after_seconds)} с — ${RESPONSE_STATUS_TITLES[str(r.status)] ?? str(r.status)}: «${str(r.text)}»`,
                    )
                    .join("; ")}
            </p>
            {((scenario.body.service_replies as Body[]) ?? []).length > 0 && (
              <p className="text-muted-foreground">
                Реплики дежурного от модели, ждут утверждения:{" "}
                {
                  ((scenario.body.service_replies as Body[]) ?? []).filter(
                    (r) => !r.approved,
                  ).length
                }
              </p>
            )}
          </>
        )}
        <div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">
              {isCall ? "Описание со слов заявителя" : "Описание в карточке"}
            </span>
            {!editing && !scenario.reference_approved && (
              <Button
                variant="ghost"
                size="sm"
                onClick={startEdit}
                aria-label="Изменить описание"
              >
                <Pencil /> Изменить
              </Button>
            )}
          </div>
          {editing ? (
            <div className="mt-1 space-y-2">
              <textarea
                className={textareaClass}
                rows={3}
                value={text}
                onChange={(e) => setText(e.target.value)}
                aria-label="Описание"
              />
              <div className="flex gap-2">
                <Button
                  size="sm"
                  onClick={() => void save()}
                  disabled={update.isPending}
                >
                  Сохранить
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setEditing(false)}
                >
                  Отмена
                </Button>
              </div>
              {update.isError && (
                <p className="text-destructive">{update.error.message}</p>
              )}
            </div>
          ) : (
            <p className="mt-1">{description || "—"}</p>
          )}
        </div>
        {approve.isError && (
          <p className="text-destructive">{approve.error.message}</p>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------- replies

function Replies({ scenario }: { scenario: ScenarioOut }) {
  const options = useScenarioOptions();
  const approveReplies = useApproveReplies(scenario.id);
  const addReply = useAddReply(scenario.id);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [adding, setAdding] = useState(false);
  const [newTopic, setNewTopic] = useState("unknown");
  const [newText, setNewText] = useState("");
  const pending = scenario.replies.filter((r) => !r.approved);
  const topics = options.data?.topics ?? [];

  const toggle = (id: number) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const approveSelected = async () => {
    await approveReplies.mutateAsync({
      reply_ids: [...selected],
      confirm_grammar: true,
    });
    setSelected(new Set());
  };

  const submitNew = async (e: FormEvent) => {
    e.preventDefault();
    await addReply.mutateAsync({ topic: newTopic, text: newText.trim() });
    setNewText("");
    setAdding(false);
  };

  return (
    <Card data-testid="replies">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-base">
          Реплики заявителя{" "}
          <span className="font-normal text-muted-foreground">
            {scenario.replies.length - pending.length}/{scenario.replies.length}{" "}
            утверждено
          </span>
        </CardTitle>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setAdding(true)}
            disabled={adding}
          >
            <Plus /> Добавить
          </Button>
          {selected.size > 0 && (
            <Button
              size="sm"
              onClick={() => void approveSelected()}
              disabled={approveReplies.isPending}
            >
              <Check /> Утвердить отмеченные ({selected.size})
            </Button>
          )}
          {pending.length > 0 && selected.size === 0 && (
            <Button
              size="sm"
              onClick={() =>
                approveReplies.mutate({
                  reply_ids: null,
                  confirm_grammar: true,
                })
              }
              disabled={approveReplies.isPending}
            >
              {approveReplies.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <CheckCheck />
              )}{" "}
              Утвердить все реплики
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {options.data && !options.data.tts_available && (
          <p className="text-xs text-muted-foreground">
            Синтез речи не запущен: утверждённые реплики останутся без озвучки,
            можно загрузить свою запись.
          </p>
        )}
        {approveReplies.isError && (
          <p className="text-sm text-destructive">
            {approveReplies.error.message}
          </p>
        )}
        {adding && (
          <form
            className="flex flex-wrap items-end gap-2 rounded border p-2"
            onSubmit={(e) => void submitNew(e)}
          >
            <div>
              <Label htmlFor="new-topic">Тема</Label>
              <select
                id="new-topic"
                className={selectClass}
                value={newTopic}
                onChange={(e) => setNewTopic(e.target.value)}
              >
                {topics.map((t) => (
                  <option key={t.code} value={t.code}>
                    {t.title}
                  </option>
                ))}
              </select>
            </div>
            <div className="min-w-60 flex-1">
              <Label htmlFor="new-text">Текст реплики</Label>
              <Input
                id="new-text"
                required
                minLength={1}
                maxLength={400}
                value={newText}
                onChange={(e) => setNewText(e.target.value)}
              />
            </div>
            <Button type="submit" size="sm" disabled={addReply.isPending}>
              Добавить
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setAdding(false)}
            >
              Отмена
            </Button>
            {addReply.isError && (
              <p className="w-full text-sm text-destructive">
                {addReply.error.message}
              </p>
            )}
          </form>
        )}
        <ul className="divide-y" aria-label="Реплики заявителя">
          {scenario.replies.map((reply) => (
            <ReplyRow
              key={reply.id}
              scenario={scenario}
              reply={reply}
              topics={topics}
              selected={selected.has(reply.id)}
              onToggle={() => toggle(reply.id)}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function ReplyRow({
  scenario,
  reply,
  topics,
  selected,
  onToggle,
}: {
  scenario: ScenarioOut;
  reply: ReplyOut;
  topics: { code: string; title: string }[];
  selected: boolean;
  onToggle: () => void;
}) {
  const edit = useEditReply(scenario.id);
  const remove = useDeleteReply(scenario.id);
  const upload = useUploadReplyAudio(scenario.id);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(reply.text);
  const [topic, setTopic] = useState(reply.topic);

  const save = async () => {
    await edit.mutateAsync({ reply_id: reply.id, text: text.trim(), topic });
    setEditing(false);
  };

  return (
    <li className="flex items-start gap-3 py-2 text-sm" data-reply={reply.id}>
      {!reply.approved ? (
        <input
          type="checkbox"
          className="mt-1"
          checked={selected}
          onChange={onToggle}
          aria-label={`Отметить реплику ${reply.id}`}
        />
      ) : (
        <Check
          className="mt-0.5 size-4 shrink-0 text-success"
          aria-label="Утверждена"
        />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Badge tone="neutral">{reply.topic_title}</Badge>
          <span>№{reply.id}</span>
          {reply.source === "generated" && !reply.approved && (
            <Badge tone="warning">создана в разговоре, на утверждение</Badge>
          )}
          {reply.approved && (
            <span>{VOICING_TITLES[reply.voicing] ?? reply.voicing}</span>
          )}
        </div>
        {editing ? (
          <div className="mt-1 space-y-2">
            <select
              className={selectClass}
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              aria-label="Тема реплики"
            >
              {topics.map((t) => (
                <option key={t.code} value={t.code}>
                  {t.title}
                </option>
              ))}
            </select>
            <textarea
              className={textareaClass}
              rows={2}
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-label="Текст реплики"
            />
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={() => void save()}
                disabled={edit.isPending || !text.trim()}
              >
                Сохранить
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setEditing(false)}
              >
                Отмена
              </Button>
            </div>
            {edit.isError && (
              <p className="text-destructive">{edit.error.message}</p>
            )}
          </div>
        ) : (
          <p className="mt-0.5">{reply.text}</p>
        )}
        {(remove.isError || upload.isError) && (
          <p className="text-destructive">
            {(remove.error ?? upload.error)?.message}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {reply.audio_url && (
          <PlayButton
            url={reply.audio_url}
            label={`Прослушать реплику ${reply.id}`}
          />
        )}
        <label
          className="inline-flex"
          title="Загрузить свою запись (WAV или MP3)"
        >
          <span className="sr-only">Загрузить запись реплики {reply.id}</span>
          <input
            type="file"
            accept=".wav,.mp3,audio/wav,audio/mpeg"
            className="sr-only"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) upload.mutate({ reply_id: reply.id, file });
              e.target.value = "";
            }}
          />
          <span className="inline-flex size-8 cursor-pointer items-center justify-center rounded-md hover:bg-accent">
            {upload.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Mic className="size-4" />
            )}
          </span>
        </label>
        {!reply.approved && (
          <>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setEditing((v) => !v)}
              aria-label={`Изменить реплику ${reply.id}`}
            >
              <Pencil />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => remove.mutate(reply.id)}
              disabled={remove.isPending}
              aria-label={`Удалить реплику ${reply.id}`}
            >
              <Trash2 />
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

/** Plays a protected media file: fetched with the bearer token, then played from a blob. */
export function PlayButton({ url, label }: { url: string; label: string }) {
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(
    () => () => {
      audioRef.current?.pause();
    },
    [],
  );

  const play = async () => {
    if (state === "playing") {
      audioRef.current?.pause();
      setState("idle");
      return;
    }
    setState("loading");
    try {
      const token = getAccessToken();
      const response = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        credentials: "include",
      });
      if (!response.ok) throw new Error("Аудиофайл недоступен");
      const blob = await response.blob();
      const audio = new Audio(URL.createObjectURL(blob));
      audioRef.current = audio;
      audio.onended = () => setState("idle");
      await audio.play();
      setState("playing");
    } catch {
      setState("idle");
    }
  };

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => void play()}
      aria-label={label}
      disabled={state === "loading"}
    >
      {state === "loading" ? (
        <Loader2 className="animate-spin" />
      ) : state === "playing" ? (
        <Square />
      ) : (
        <Play />
      )}
    </Button>
  );
}

// ---------------------------------------------------------------- preview dialog

function PreviewDialog({ scenario }: { scenario: ScenarioOut }) {
  const preview = usePreviewDialog(scenario.id);
  const [history, setHistory] = useState<
    (PreviewTurnIn & { audio_url?: string | null })[]
  >([]);
  const [text, setText] = useState("");
  const opening = str(obj(scenario.body.caller).opening);

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const phrase = text.trim();
    if (!phrase) return;
    const result = await preview.mutateAsync({ text: phrase, history });
    setHistory((h) => [
      ...h,
      { role: "operator", text: phrase },
      { role: "caller", text: result.reply, audio_url: result.audio_url },
    ]);
    setText("");
  };

  return (
    <Card data-testid="preview-dialog">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <MessageSquare className="size-4" aria-hidden /> Поговорить с
          заявителем
        </CardTitle>
        {history.length > 0 && (
          <Button variant="ghost" size="sm" onClick={() => setHistory([])}>
            Начать заново
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-xs text-muted-foreground">
          Разговор до утверждения: отвечают все реплики, включая неутверждённые;
          ничего не сохраняется.
        </p>
        <ul className="max-h-64 space-y-1 overflow-y-auto" aria-live="polite">
          <li className="text-muted-foreground">Заявитель: «{opening}»</li>
          {history.map((turn, i) => (
            <li
              key={i}
              className={turn.role === "operator" ? "text-right" : ""}
            >
              <span className="text-muted-foreground">
                {turn.role === "operator" ? "Вы" : "Заявитель"}:
              </span>{" "}
              {turn.text}
              {turn.audio_url && (
                <PlayButton url={turn.audio_url} label="Прослушать ответ" />
              )}
            </li>
          ))}
        </ul>
        <form className="flex gap-2" onSubmit={(e) => void send(e)}>
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Скажите адрес"
            aria-label="Ваша фраза"
          />
          <Button type="submit" disabled={preview.isPending || !text.trim()}>
            {preview.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              "Сказать"
            )}
          </Button>
        </form>
        {preview.isError && (
          <p className="text-destructive">{preview.error.message}</p>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------- revise and versions

function Revise({ scenario }: { scenario: ScenarioOut }) {
  const revise = useReviseScenario(scenario.id);
  const [comment, setComment] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const refetch = useScenario(scenario.id).refetch;

  useEffect(() => {
    if (job.data?.status === "done") {
      void refetch();
      setJobId(null);
      setComment("");
    }
  }, [job.data?.status, refetch]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const accepted = await revise.mutateAsync(comment.trim());
    setJobId(accepted.job_id);
  };
  const running = Boolean(jobId) && job.data?.status !== "failed";

  return (
    <Card data-testid="revise">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <RefreshCw className="size-4" aria-hidden /> Что исправить
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p className="text-xs text-muted-foreground">
          Переделка создаёт новую версию по вашему замечанию. Утверждённые
          реплики и утверждённый эталон переходят в неё без изменений.
        </p>
        <form className="space-y-2" onSubmit={(e) => void submit(e)}>
          <textarea
            className={textareaClass}
            rows={2}
            minLength={3}
            maxLength={1000}
            required
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Сделай заявителя более растерянным, добавь реплики про подъезд"
            aria-label="Что исправить"
            disabled={running}
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="submit"
              variant="outline"
              disabled={
                running || revise.isPending || comment.trim().length < 3
              }
            >
              {running ? <Loader2 className="animate-spin" /> : <RefreshCw />}{" "}
              Переделать
            </Button>
            {revise.isError && (
              <span className="text-destructive">{revise.error.message}</span>
            )}
            {jobId && job.data && <JobProgress job={job.data} />}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function Versions({ scenario }: { scenario: ScenarioOut }) {
  const versions = useMemo(() => scenario.versions, [scenario.versions]);
  if (versions.length <= 1) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <History className="size-4" aria-hidden /> Версии
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="divide-y text-sm">
          {versions.map((v) => (
            <li
              key={v.version}
              className="flex flex-wrap items-center justify-between gap-2 py-1.5"
            >
              <span>
                Версия {v.version}
                {v.is_current && (
                  <Badge tone="primary" className="ml-2">
                    текущая
                  </Badge>
                )}
                {v.revision_comment && (
                  <span className="text-muted-foreground">
                    {" "}
                    — {v.revision_comment}
                  </span>
                )}
              </span>
              <span className="text-xs text-muted-foreground">
                {formatDateTime(v.created_at)}
                {v.created_by ? ` · ${v.created_by}` : ""}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
