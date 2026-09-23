import { BookOpen, Check, CircleAlert, FilePlus2, Loader2, X } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { callInfo, useDialog } from "@/api/intake";
import { useScenarioFromAttempt } from "@/api/scenarios";
import { getAccessToken } from "@/api/token";
import { useServices } from "@/api/teacher";
import type { AttemptOut } from "@/api/training";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatSeconds, formatTime } from "@/emulator/time";
import { EMPTY_ADDRESS } from "@/intake/draft";
import { cn } from "@/lib/utils";
import { ScoreBox } from "@/review/teacher-panel";
import { END_REASON_LABELS } from "@/softphone/context";

// Shapes of `attempt.evaluation` (EvaluationResult.to_dict()) and `attempt.reference`
// (the scenario's reference_card) for a call-intake attempt (PRD 9.3).
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
export interface CallEvaluation {
  total: number;
  passed: boolean;
  components: Record<string, Component>;
  errors: ErrorItem[];
  methods: Record<string, string>;
  ai_comment: string | null;
  checked_text?: string;
}
interface ReferenceCard {
  signs_path: string[];
  incident_type: string;
  flags: Record<string, boolean>;
  address: Record<string, string | null>;
  caller: { name?: string | null; role?: string | null; phone?: string | null };
  description_keywords: string[];
  description?: string | null;
  expected_services: string[];
}

const COMPONENT_ORDER = ["survey_card", "flags_services", "address", "required_topics", "description", "time", "typical_errors", "grammar"];
const ADDRESS_PARTS: [string, string][] = [
  ["region", "Регион"],
  ["city", "Населённый пункт"],
  ["street", "Улица"],
  ["house", "Дом"],
  ["building", "Корпус"],
  ["structure", "Строение"],
  ["entrance", "Подъезд"],
  ["floor", "Этаж"],
  ["apartment", "Квартира"],
  ["code", "Код"],
  ["descriptive", "Описательный адрес"],
];
const FLAG_TITLES: Record<string, string> = {
  injured: "Пострадавшие",
  not_on_site: "Нет на месте / отказ от скорой",
  no_access: "Нет доступа",
  threat: "Угроза людям",
  gasification: "Газификация",
  offense: "Правонарушение",
};

/** Review of a call-intake attempt (PRD 13.6): survey card, address, services and topics
 * against the reference, the transcript with the recording, errors, the description. */
export function CallReview({ attempt, evaluation, teacher }: { attempt: AttemptOut; evaluation: CallEvaluation; teacher: boolean }) {
  const reference = attempt.reference as ReferenceCard | null;
  const draft = (attempt.intake?.draft ?? {}) as Record<string, unknown>;
  const dialog = useDialog(attempt.id);
  const services = useServices();
  const titles = new Map((services.data ?? []).map((s) => [s.code, s.short_title]));
  const components = COMPONENT_ORDER.map((k) => evaluation.components[k]).filter((c): c is Component => Boolean(c));
  const survey = evaluation.components.survey_card?.items[0] as { matched_levels?: number; levels?: number; note?: string } | undefined;
  const flagsItem = evaluation.components.flags_services?.items[0] as { flags_wrong?: string[] } | undefined;
  const servicesItem = evaluation.components.flags_services?.items[1] as { missing?: string[]; extra?: string[] } | undefined;
  const addressItems = (evaluation.components.address?.items ?? []) as { part: string; match: number; note: string }[];
  const topicsItem = evaluation.components.required_topics?.items[0] as { required?: string[]; covered?: string[]; missing?: string[] } | undefined;
  const timeItem = evaluation.components.time?.items[0] as { seconds?: number | null; norm_seconds?: number } | undefined;
  const keywords = evaluation.components.description?.items[0] as { keywords_found?: string[]; keywords_missing?: string[] } | undefined;
  const grammar = evaluation.components.grammar;
  const actualPath = (draft.signs_path as string[] | undefined) ?? [];
  const actualAddress = { ...EMPTY_ADDRESS, ...((draft.address as Record<string, string>) ?? {}) };
  const actualFlags = (draft.flags as Record<string, boolean> | undefined) ?? {};
  const actualServices = (draft.services as string[] | undefined) ?? [];
  const call = callInfo(dialog.data, true);
  const topicTitle = (code: string) => dialog.data?.topics.find((t) => t.code === code)?.title ?? code;
  const back = teacher ? `/teacher/sessions/${attempt.session.id}` : `/student/sessions/${attempt.session.id}/calls`;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to={teacher ? `/teacher/sessions/${attempt.session.id}/report` : back} className="text-sm text-muted-foreground hover:underline">
            ← {teacher ? "К отчёту" : "К вызовам"}
          </Link>
          <p className="text-sm text-muted-foreground">
            Разбор · вызов {attempt.card.number}
            {teacher && ` · ${attempt.arm.dispatcher}`}
          </p>
          <h1 className="text-2xl font-semibold">{attempt.intake?.title || "Приём вызова"}</h1>
          {timeItem && (
            <p className="text-sm text-muted-foreground">
              Разговор и карточка: {timeItem.seconds == null ? "—" : formatSeconds(timeItem.seconds)} при нормативе {formatSeconds(timeItem.norm_seconds ?? attempt.norm_seconds)}
              {timeItem.seconds != null && timeItem.seconds > (timeItem.norm_seconds ?? attempt.norm_seconds) ? " — дольше норматива." : "."}
            </p>
          )}
        </div>
        <ScoreBox total={evaluation.total} passed={evaluation.passed} override={attempt.override} reasons={evaluation.errors.filter((e) => e.critical).map((e) => e.explanation)} />
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
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Опросная карта</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <PathRow label="Эталон" path={reference?.signs_path ?? []} against={actualPath} good />
            <PathRow label={teacher ? "Обучающийся" : "Вы"} path={actualPath} against={reference?.signs_path ?? []} />
            {survey?.note && <p className="text-xs text-muted-foreground">{survey.note}</p>}
            <div>
              <div className="mb-1 text-xs text-muted-foreground">Признаки</div>
              <div className="flex flex-wrap gap-1">
                {Array.from(new Set([...Object.keys(reference?.flags ?? {}), ...Object.keys(actualFlags).filter((k) => actualFlags[k])])).map((code) => {
                  const wrong = flagsItem?.flags_wrong?.includes(code);
                  return (
                    <span key={code} className={cn("rounded-md border px-2 py-0.5 text-xs", wrong ? "border-destructive text-destructive" : "border-success/60")} title={wrong ? "не совпало с эталоном" : "как в эталоне"}>
                      {FLAG_TITLES[code] ?? code}: {actualFlags[code] ? "да" : "нет"}
                      {wrong && ` (эталон: ${reference?.flags?.[code] ? "да" : "нет"})`}
                    </span>
                  );
                })}
              </div>
            </div>
            <div>
              <div className="mb-1 text-xs text-muted-foreground">Службы</div>
              <div className="flex flex-wrap gap-1" data-testid="review-services">
                {actualServices.map((code) => (
                  <span key={code} className={cn("rounded-md border px-2 py-0.5 text-xs", servicesItem?.extra?.includes(code) ? "border-warning text-warning" : "border-success/60")}>
                    {titles.get(code) ?? code}
                  </span>
                ))}
                {(servicesItem?.missing ?? []).map((code) => (
                  <span key={`m-${code}`} className="rounded-md border border-destructive px-2 py-0.5 text-xs text-destructive line-through">
                    {titles.get(code) ?? code}
                  </span>
                ))}
              </div>
              {(servicesItem?.missing?.length ?? 0) > 0 && <p className="mt-1 text-xs text-muted-foreground">Зачёркнуты службы эталона, которых не было в карточке; жёлтые — лишние.</p>}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Адрес и заявитель</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <table className="w-full text-xs">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-1 text-left font-normal">Поле</th>
                  <th className="py-1 text-left font-normal">{teacher ? "Обучающийся" : "Вы"}</th>
                  <th className="py-1 text-left font-normal">Эталон</th>
                </tr>
              </thead>
              <tbody>
                {ADDRESS_PARTS.filter(([k]) => reference?.address?.[k] || actualAddress[k as keyof typeof actualAddress]).map(([k, label]) => {
                  const item = addressItems.find((i) => i.part === k);
                  const ok = item ? item.match >= 1 : undefined;
                  return (
                    <tr key={k} className="border-t">
                      <td className="py-1 text-muted-foreground">{label}</td>
                      <td className={cn("py-1", ok === false && "text-destructive", ok === true && "text-success")}>
                        {actualAddress[k as keyof typeof actualAddress] || "—"}
                        {item && <span className="ml-1 text-muted-foreground">({item.note})</span>}
                      </td>
                      <td className="py-1">{reference?.address?.[k] || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p>
              <span className="text-muted-foreground">Заявитель:</span> {(draft.caller as { name?: string })?.name || "—"}
              {(draft.caller as { role?: string })?.role && ` (${(draft.caller as { role?: string }).role})`}
              {reference?.caller?.name && <span className="text-muted-foreground"> · эталон: {reference.caller.name}{reference.caller.role ? ` (${reference.caller.role})` : ""}</span>}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Обязательные вопросы</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            <ul className="grid grid-cols-2 gap-1" aria-label="Темы">
              {(topicsItem?.required ?? attempt.intake?.required_topics ?? []).map((code) => {
                const covered = topicsItem?.covered?.includes(code);
                return (
                  <li key={code} className={cn("flex items-center gap-1", covered ? "text-success" : "text-destructive")} data-topic={code} data-covered={covered}>
                    {covered ? <Check className="size-4" aria-hidden /> : <X className="size-4" aria-hidden />}
                    {topicTitle(code)}
                  </li>
                );
              })}
            </ul>
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
                      <Link to={`/student/reference?q=${encodeURIComponent(e.title)}`} className="mt-1 inline-flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline">
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
          <CardTitle className="text-base">Стенограмма разговора</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {call.recording_available && <TokenAudio url={`/api/attempts/${attempt.id}/recording`} label="Запись звонка" className="w-full" />}
          {dialog.isPending ? (
            <p className="text-muted-foreground">Загружаем стенограмму…</p>
          ) : dialog.data && dialog.data.turns.length > 0 ? (
            <ol className="space-y-2" aria-label="Реплики">
              {dialog.data.turns.map((t) => (
                <li key={t.index} className="flex items-start gap-2" data-role={t.role}>
                  <span className="w-14 shrink-0 font-mono text-xs text-muted-foreground">{formatTime(t.at)}</span>
                  <span className="w-20 shrink-0 text-xs text-muted-foreground">{t.role === "operator" ? "оператор" : "заявитель"}</span>
                  <span className="flex-1">
                    {t.text}
                    {(t.topics ?? []).map((topic) => (
                      <span key={topic} className={cn("ml-1 rounded-sm px-1 text-xs", topicsItem?.required?.includes(topic) ? "bg-success/15 text-success" : "bg-muted text-muted-foreground")}>
                        {topicTitle(topic)}
                      </span>
                    ))}
                    {t.audio_url && <TokenAudio url={t.audio_url} label="Реплика заявителя" className="mt-1 h-8 w-64" />}
                  </span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-muted-foreground">Разговора не было: карточка сохранена без ответа на вызов.</p>
          )}
          {call.end_reason && <p className="text-xs text-muted-foreground">Завершение звонка: {END_REASON_LABELS[call.end_reason] ?? call.end_reason}.</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Описание со слов заявителя</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {evaluation.checked_text ? (
            <p className="whitespace-pre-line rounded-md bg-muted p-3 leading-relaxed">
              <Underlined text={evaluation.checked_text} items={grammar?.status === "checked" ? grammar.items : []} />
            </p>
          ) : (
            <p className="text-muted-foreground">Описание не заполнено.</p>
          )}
          {keywords && (
            <p className="text-xs text-muted-foreground">
              Ключевые слова эталона: {(keywords.keywords_found ?? []).map((k) => <span key={k} className="mr-1 text-success">{k}</span>)}
              {(keywords.keywords_missing ?? []).map((k) => <span key={k} className="mr-1 text-destructive line-through">{k}</span>)}
            </p>
          )}
          {reference?.description && <p className="text-xs text-muted-foreground">Эталон: {reference.description}</p>}
          {grammar?.status === "not_checked" && <p className="text-xs text-muted-foreground">Проверка грамотности недоступна: LanguageTool не ответил.</p>}
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
          <Link to={back}>{teacher ? "К занятию" : "Следующий вызов"}</Link>
        </Button>
        {teacher && (
          <Button asChild variant="outline">
            <Link to={`/teacher/sessions/${attempt.session.id}/report`}>К отчёту</Link>
          </Button>
        )}
        {teacher && <ToScenarioButton attemptId={attempt.id} />}
      </div>
    </div>
  );
}

/** PRD 9.6: the saved card becomes a card_response scenario the teacher approves in the library. */
function ToScenarioButton({ attemptId }: { attemptId: string }) {
  const create = useScenarioFromAttempt();
  const navigate = useNavigate();
  return (
    <>
      <Button
        variant="outline"
        disabled={create.isPending}
        onClick={() => void create.mutateAsync(attemptId).then((s) => navigate(`/teacher/scenarios/${s.id}`))}
      >
        {create.isPending ? <Loader2 className="animate-spin" /> : <FilePlus2 />} Сделать сценарием реагирования
      </Button>
      {create.isError && (
        <p className="w-full text-sm text-destructive" role="alert">
          {create.error.message}
        </p>
      )}
    </>
  );
}

/** Media endpoints need the bearer token, which <audio src> cannot send: fetched on demand. */
export function TokenAudio({ url, label, className }: { url: string; label: string; className?: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  if (src) return <audio controls autoPlay src={src} className={className} aria-label={label} />;
  return (
    <button
      type="button"
      disabled={failed}
      onClick={async () => {
        try {
          const res = await fetch(url, { headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` } });
          if (!res.ok) throw new Error(String(res.status));
          setSrc(URL.createObjectURL(await res.blob()));
        } catch {
          setFailed(true);
        }
      }}
      className={cn("inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs hover:bg-muted disabled:opacity-50", className)}
      aria-label={label}
    >
      ▶ {failed ? "аудио недоступно" : label.toLowerCase()}
    </button>
  );
}

function PathRow({ label, path, against, good }: { label: string; path: string[]; against: string[]; good?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="w-28 text-xs text-muted-foreground">{label}</span>
      {path.length === 0 && <span className="text-muted-foreground">тип не выбран</span>}
      {path.map((sign, i) => {
        const same = against[i] === sign;
        return (
          <span key={i} className="flex items-center gap-1">
            {i > 0 && <span className="text-muted-foreground">›</span>}
            <span className={cn("rounded-md border px-2 py-0.5 text-xs", same ? "border-success/60 bg-success/10" : good ? "border-muted" : "border-destructive text-destructive")}>
              {sign}
            </span>
            {!same && !good && <CircleAlert className="size-3.5 text-destructive" aria-label="не совпало" />}
          </span>
        );
      })}
    </div>
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
