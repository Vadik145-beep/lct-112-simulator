import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import {
  useClassifierTree,
  useCreateSession,
  useGroups,
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
import { DIFFICULTY_TITLES } from "@/teacher/labels";
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
};

const selectClass =
  "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50";

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
      unfinished_seconds: Number.isFinite(hours) && hours > 0 ? Math.round(hours * HOUR) : DEFAULTS.unfinished_seconds,
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
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <input type="radio" name="mode" disabled />
              Приём вызова (оператор 112) — появится позже
            </label>
          </fieldset>
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
          <fieldset>
            <legend className="mb-2 text-sm font-medium">Профиль службы</legend>
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
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cards">Карточек на обучающегося</Label>
            <Input
              id="cards"
              type="number"
              min={0}
              max={50}
              value={form.cards_per_student}
              onChange={(e) => patch({ cards_per_student: Number(e.target.value) })}
            />
            <p className="text-xs text-muted-foreground">0 — вся очередь сценариев.</p>
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
            <p className="text-xs text-muted-foreground">От «Добавлена» до первичного статуса.</p>
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
