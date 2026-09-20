import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import {
  useClassifierTree,
  useCreateSession,
  useGroups,
  useModels,
  useQueuePreview,
  useServices,
  useTeacherSession,
  useUpdateSession,
  type SessionIn,
  type SessionOut,
} from "@/api/teacher";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { CARDS_AT_ONCE, DIFFICULTY_TITLES } from "@/teacher/labels";
import { cn } from "@/lib/utils";

const HOUR = 3600;
const DEFAULTS: Omit<SessionIn, "group_id"> = {
  title: "",
  mode: "card_response",
  card_source: "scenarios",
  scenario_ids: [],
  incident_groups: [],
  difficulty: 1,
  service_profile: [],
  norm_seconds: 30,
  pass_threshold: 70,
  hints_enabled: true,
  cards_per_student: 0,
  unfinished_seconds: 48 * HOUR,
  weights: {},
  voice_enabled: false,
  dialog_mode: "select",
  adaptive: false,
};

// PRD 9.3: the caller answers with an approved reply (select), may improvise with the
// teacher approving new lines (hybrid), improvises freely (generate), or the trainee
// presses topic buttons (buttons, no model).
// «Источник карточек» of the ТЗ: «scenarios» is the value older sessions carry; the server
// treats it and «mixed» alike.
const CARD_SOURCES = [
  { code: "scenarios", title: "Все утверждённые сценарии" },
  { code: "generated", title: "Билеты и сгенерированные (без карточек обучающихся)" },
  { code: "student_made", title: "Только карточки обучающихся" },
];

const DIALOG_MODES: { code: string; title: string; hint: string }[] = [
  { code: "select", title: "Готовые реплики", hint: "модель выбирает утверждённую реплику; режим стенда" },
  { code: "hybrid", title: "Готовые + новые", hint: "если реплики нет, модель сочиняет; новое — на утверждение после занятия" },
  { code: "generate", title: "Свободная генерация", hint: "модель сочиняет каждую реплику; медленнее и менее предсказуемо" },
  { code: "buttons", title: "Кнопки тем", hint: "без модели" },
];
// plan/track-c-vapi.md: the caller lives in Vapi; offered only where the stand enables it
// (ALLOW_EXTERNAL_AI=true, outside the closed contour).
const CLOUD_MODE = { code: "cloud", title: "Облачный голос", hint: "заявителя играет облачная модель с живым голосом; демо вне закрытого контура" };
const NORM_DEFAULT = { card_response: 30, call_intake: 90 } as const;

const selectClass =
  "flex h-9 w-full rounded-md border border-input bg-background text-foreground px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50";

export function SessionFormPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  if (sessionId) return <EditSession sessionId={sessionId} />;
  return <SessionForm />;
}

function EditSession({ sessionId }: { sessionId: string }) {
  const query = useTeacherSession(sessionId);
  if (query.isPending) return <LoadingState text="Загружаем занятие…" />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  if (query.data.status !== "draft") return <Navigate to={`/teacher/sessions/${sessionId}`} replace />;
  return <SessionForm existing={query.data} />;
}

function toInput(s: SessionOut): SessionIn {
  return {
    title: s.title,
    mode: s.mode,
    group_id: s.group_id ?? "",
    card_source: s.card_source,
    scenario_ids: s.scenario_ids,
    incident_groups: s.incident_groups,
    difficulty: s.difficulty,
    service_profile: s.service_profile,
    norm_seconds: s.norm_seconds,
    pass_threshold: s.pass_threshold,
    hints_enabled: s.hints_enabled,
    cards_per_student: s.cards_per_student,
    unfinished_seconds: s.unfinished_seconds,
    weights: s.weights,
    voice_enabled: s.voice_enabled,
    dialog_mode: s.dialog_mode,
    adaptive: s.adaptive,
  };
}

/** Create or edit (while a draft) a lesson: PRD 13.7, the teacher's «создание занятия». */
function SessionForm({ existing }: { existing?: SessionOut }) {
  const navigate = useNavigate();
  const groups = useGroups();
  const tree = useClassifierTree();
  const services = useServices();
  const create = useCreateSession();
  const update = useUpdateSession(existing?.id ?? "");
  const [form, setForm] = useState<SessionIn>(() => (existing ? toInput(existing) : { ...DEFAULTS, group_id: "" }));
  const models = useModels(form.mode === "call_intake");
  const preview = useQueuePreview({
    mode: form.mode,
    card_source: form.card_source,
    scenario_ids: form.scenario_ids ?? [],
    incident_groups: form.incident_groups ?? [],
    difficulty: form.difficulty,
    service_profile: form.service_profile ?? [],
  });
  const [unfinishedHours, setUnfinishedHours] = useState(() => String(Math.round((existing?.unfinished_seconds ?? DEFAULTS.unfinished_seconds!) / HOUR)));
  const mutation = existing ? update : create;

  if (groups.isPending || tree.isPending || services.isPending) return <LoadingState text="Готовим форму…" />;
  if (groups.isError) return <ErrorState message={groups.error.message} onRetry={() => void groups.refetch()} />;
  if (tree.isError) return <ErrorState message={tree.error.message} onRetry={() => void tree.refetch()} />;
  if (services.isError) return <ErrorState message={services.error.message} onRetry={() => void services.refetch()} />;

  const groupId = form.group_id || groups.data[0]?.id || "";
  const patch = (changes: Partial<SessionIn>) => setForm((prev) => ({ ...prev, ...changes }));
  const toggle = (key: "incident_groups" | "service_profile", code: string) =>
    setForm((prev) => {
      const list = prev[key] ?? [];
      return { ...prev, [key]: list.includes(code) ? list.filter((c) => c !== code) : [...list, code] };
    });

  function submit(e: FormEvent) {
    e.preventDefault();
    const hours = Number(unfinishedHours);
    const body: SessionIn = {
      ...form,
      title: form.title.trim(),
      group_id: groupId,
      unfinished_seconds: Number.isFinite(hours) && hours > 0 ? Math.round(hours * HOUR) : (DEFAULTS.unfinished_seconds ?? null),
    };
    mutation.mutate(body, { onSuccess: (data) => navigate(`/teacher/sessions/${data.id}`) });
  }

  return (
    <form onSubmit={submit} className="space-y-6" aria-label={existing ? "Настройки занятия" : "Новое занятие"}>
      <div>
        <Link to={existing ? `/teacher/sessions/${existing.id}` : "/teacher"} className="text-sm text-muted-foreground hover:underline">
          ← {existing ? "К занятию" : "К списку занятий"}
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">{existing ? "Настройки занятия" : "Новое занятие"}</h1>
        <p className="text-sm text-muted-foreground">
          Карточки выдаются группе после нажатия «Начать занятие»; до этого настройки можно менять.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Что и кому</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="title">Название</Label>
            <Input
              id="title"
              required
              maxLength={300}
              value={form.title}
              onChange={(e) => patch({ title: e.target.value })}
              placeholder="Например: Реагирование ДДС управы, занятие 3"
            />
          </div>
          <fieldset className="space-y-1.5">
            <legend className="text-sm font-medium">Режим</legend>
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" name="mode" checked={form.mode === "card_response"} onChange={() => patch({ mode: "card_response" })} />
              Реагирование на карточку (ДДС)
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="mode"
                checked={form.mode === "call_intake"}
                onChange={() => patch({ mode: "call_intake", service_profile: [], norm_seconds: form.norm_seconds === NORM_DEFAULT.card_response ? NORM_DEFAULT.call_intake : form.norm_seconds })}
              />
              Приём вызова (оператор 112)
            </label>
          </fieldset>
          {form.mode === "call_intake" && (
            <div className="space-y-3 sm:col-span-2" data-testid="call-intake-settings">
              <div className="space-y-1.5">
                <Label htmlFor="dialog-mode">Как отвечает заявитель</Label>
                <select id="dialog-mode" className={selectClass} value={form.dialog_mode} onChange={(e) => patch({ dialog_mode: e.target.value })}>
                  {[...DIALOG_MODES, ...(models.data?.cloud || form.dialog_mode === "cloud" ? [CLOUD_MODE] : [])].map((m) => (
                    <option key={m.code} value={m.code}>
                      {m.title} — {m.hint}
                    </option>
                  ))}
                </select>
                {form.dialog_mode === "cloud" && (
                  <p className="text-xs text-muted-foreground" data-testid="cloud-mode-note">
                    Голос оператора и выдуманные данные билета уходят во внешний облачный сервис. Без телефонии разговор идёт прямо из браузера; если облако недоступно, заявитель отвечает локальной моделью.
                  </p>
                )}
                {models.data && !models.data.dialog && form.dialog_mode !== "buttons" && form.dialog_mode !== "cloud" && (
                  <p className="text-xs text-destructive" role="alert" data-testid="dialog-model-warning">
                    Модель диалога сейчас недоступна: заявитель будет отвечать по ключевым словам, как в режиме «Кнопки тем».
                  </p>
                )}
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.voice_enabled} onChange={(e) => patch({ voice_enabled: e.target.checked })} />
                Голос: заявитель звучит, обучающийся говорит в гарнитуру
              </label>
              <p className="text-xs text-muted-foreground">Без голоса разговор идёт текстом в панели тренажёра.</p>
              {models.data && form.voice_enabled && (!models.data.tts || !models.data.stt) && (
                <p className="text-xs text-destructive" role="alert" data-testid="voice-warning">
                  {!models.data.tts && "Озвучка недоступна: заявитель ответит текстом. "}
                  {!models.data.stt && "Распознавание речи недоступно: обучающийся сможет только писать."}
                </p>
              )}
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="card-source">Источник карточек</Label>
            <select id="card-source" className={selectClass} value={form.card_source} onChange={(e) => patch({ card_source: e.target.value })}>
              {CARD_SOURCES.map((c) => (
                <option key={c.code} value={c.code}>
                  {c.title}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              {form.mode === "card_response"
                ? "Карточки обучающихся — сохранённые в приёме вызова и утверждённые преподавателем как сценарии."
                : "В приёме вызова карточки обучающихся не участвуют: берутся сценарии из билетов и сгенерированные."}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="group">Группа</Label>
            <select id="group" className={selectClass} value={groupId} onChange={(e) => patch({ group_id: e.target.value })} required>
              {groups.data.length === 0 && <option value="">Сначала создайте группу</option>}
              {groups.data.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.title} · {g.members.length} чел.
                </option>
              ))}
            </select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Карточки</CardTitle>
          <CardDescription>
            Источник — утверждённые сценарии. Без отметок берутся все группы происшествий и все службы.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-6 lg:grid-cols-2">
          <fieldset>
            <legend className="mb-2 text-sm font-medium">Группы происшествий</legend>
            <div className="grid max-h-64 gap-1 overflow-y-auto rounded-md border p-2 sm:grid-cols-2">
              {tree.data.groups.map((g) => (
                <label key={g.code} className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={form.incident_groups?.includes(g.code) ?? false}
                    onChange={() => toggle("incident_groups", g.code)}
                  />
                  <span>
                    <span className="text-muted-foreground">{g.code}.</span> {g.title}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset disabled={form.mode === "call_intake"} className={cn(form.mode === "call_intake" && "opacity-50")}>
            <legend className="mb-2 text-sm font-medium">Профиль службы{form.mode === "call_intake" ? " (оператор 112 — без службы)" : ""}</legend>
            <div className="grid max-h-64 gap-1 overflow-y-auto rounded-md border p-2 sm:grid-cols-2">
              {services.data.map((s) => (
                <label key={s.code} className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={form.service_profile?.includes(s.code) ?? false}
                    onChange={() => toggle("service_profile", s.code)}
                  />
                  <span>{s.title}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="space-y-1.5">
            <Label htmlFor="difficulty">Сложность</Label>
            <select id="difficulty" className={selectClass} value={form.difficulty} onChange={(e) => patch({ difficulty: Number(e.target.value) })}>
              {[1, 2, 3].map((d) => (
                <option key={d} value={d}>
                  {DIFFICULTY_TITLES[d]}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              {form.mode === "call_intake" ? "Оператор 112 принимает по одному вызову." : `На экране одновременно: ${CARDS_AT_ONCE[form.difficulty] ?? 1}.`}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cards">{form.mode === "call_intake" ? "Вызовов на обучающегося (всего за занятие)" : "Карточек на обучающегося (всего за занятие)"}</Label>
            <Input
              id="cards"
              type="number"
              min={0}
              max={50}
              value={form.cards_per_student}
              onChange={(e) => patch({ cards_per_student: Number(e.target.value) })}
            />
            <p className="text-xs text-muted-foreground">0 — вся очередь: каждый получает все подходящие карточки по очереди.</p>
          </div>
          <div className="lg:col-span-2" data-testid="queue-preview" aria-live="polite">
            {preview.isPending ? (
              <p className="text-xs text-muted-foreground">Считаем подходящие карточки…</p>
            ) : preview.isError ? (
              <p className="text-xs text-destructive">Не удалось посчитать карточки: {preview.error.message}</p>
            ) : preview.data.total === 0 ? (
              <p className="text-sm text-destructive" role="alert">
                Под выбранные {form.mode === "call_intake" ? "группы происшествий" : "службы и группы происшествий"} нет утверждённых сценариев — занятие не начнётся. Снимите отметки или утвердите сценарии.
              </p>
            ) : (
              <p className="text-sm">
                Подходит {form.mode === "call_intake" ? "вызовов" : "карточек"}: <b>{preview.data.total}</b>
                {preview.data.harder_only && <span className="text-muted-foreground"> — все сложнее выбранной сложности, будут выданы как есть</span>}
                {form.cards_per_student > preview.data.total && (
                  <span className="text-muted-foreground"> — меньше, чем «{form.cards_per_student} на обучающегося»: каждый получит {preview.data.total}</span>
                )}
                .
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Нормативы и оценка</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="space-y-1.5">
            <Label htmlFor="norm">Норматив, секунд</Label>
            <Input id="norm" type="number" min={5} max={600} required value={form.norm_seconds} onChange={(e) => patch({ norm_seconds: Number(e.target.value) })} />
            <p className="text-xs text-muted-foreground">{form.mode === "call_intake" ? "От ответа на вызов до сохранения карточки." : "От «Добавлена» до первичного статуса."}</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="threshold">Порог зачёта, баллов</Label>
            <Input id="threshold" type="number" min={0} max={100} required value={form.pass_threshold} onChange={(e) => patch({ pass_threshold: Number(e.target.value) })} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="unfinished">«Не завершено» через, часов</Label>
            <Input id="unfinished" type="number" min={0.05} step="any" required value={unfinishedHours} onChange={(e) => setUnfinishedHours(e.target.value)} />
            <p className="text-xs text-muted-foreground">В АРМ-112 — 48 часов после «Принята».</p>
          </div>
          <div className="space-y-1.5">
            <span className="text-sm font-medium">Подсказки</span>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.hints_enabled} onChange={(e) => patch({ hints_enabled: e.target.checked })} />
              Показывать обучающимся панель «Тренажёр»
            </label>
            <p className="text-xs text-muted-foreground">Выключите для аттестации.</p>
          </div>
          <div className="space-y-1.5">
            <span className="text-sm font-medium">Адаптивный подбор</span>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.adaptive} onChange={(e) => patch({ adaptive: e.target.checked })} />
              Подбирать карточки под слабые места
            </label>
            <p className="text-xs text-muted-foreground">Каждому — сначала его слабая группа происшествий, сложность по рейтингу, непройденные первыми.</p>
          </div>
        </CardContent>
      </Card>

      {mutation.isError && <ErrorState message={mutation.error.message} />}

      <div className={cn("flex flex-wrap gap-2")}>
        <Button type="submit" disabled={mutation.isPending || !groupId}>
          {mutation.isPending ? "Сохраняем…" : existing ? "Сохранить настройки" : "Создать занятие"}
        </Button>
        <Button type="button" variant="outline" onClick={() => navigate(existing ? `/teacher/sessions/${existing.id}` : "/teacher")}>
          Отмена
        </Button>
      </div>
    </form>
  );
}
