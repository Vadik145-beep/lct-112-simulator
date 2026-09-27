import { BookOpen, FileUp, Loader2, Sparkles, Trash2, Wand2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  useDeleteReferenceDoc,
  useGenerateScenario,
  useJob,
  useReferenceDocs,
  useScenarioOptions,
  useScenarios,
  useUploadReferenceDoc,
  type GenerateIn,
  type ScenarioFilters,
  type ScenarioListItem,
} from "@/api/scenarios";
import { useClassifierTree } from "@/api/teacher";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDate } from "@/emulator/time";
import { MODE_TITLES, plural } from "@/teacher/labels";
import {
  DIFFICULTY_SHORT,
  SCENARIO_STATUS_TITLES,
  SCENARIO_STATUS_TONES,
  SOURCE_TITLES,
} from "@/teacher/scenario-labels";

const selectClass =
  "flex h-9 w-full rounded-md border border-input bg-background text-foreground px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50";

/** «Сценарии»: the library with filters, generation by a phrase, methodical documents. */
export function TeacherScenariosPage() {
  const [filters, setFilters] = useState<ScenarioFilters>({});
  const [search, setSearch] = useState("");
  const [showGenerate, setShowGenerate] = useState(false);
  const [showDocs, setShowDocs] = useState(false);
  const list = useScenarios({ ...filters, q: search.trim() || undefined });
  const tree = useClassifierTree();
  const options = useScenarioOptions();

  const patch = (next: Partial<ScenarioFilters>) => setFilters((f) => ({ ...f, ...next }));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Сценарии</h1>
          <p className="text-sm text-muted-foreground">
            Билеты организаторов, сгенерированные и созданные вручную сценарии. Занятие берёт только утверждённые.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => setShowDocs((v) => !v)}>
            <BookOpen /> Методические материалы
          </Button>
          <Button onClick={() => setShowGenerate((v) => !v)}>
            <Sparkles /> Сгенерировать по фразе
          </Button>
        </div>
      </div>

      {showGenerate && <GenerateForm onClose={() => setShowGenerate(false)} />}
      {showDocs && <ReferenceDocs />}

      <div className="grid gap-3 md:grid-cols-6" role="search" aria-label="Фильтры сценариев">
        <div className="md:col-span-2">
          <Label htmlFor="q">Поиск по названию</Label>
          <Input id="q" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="мусоропровод, ДТП…" />
        </div>
        <div>
          <Label htmlFor="kind">Режим</Label>
          <select id="kind" className={selectClass} value={filters.kind ?? ""} onChange={(e) => patch({ kind: (e.target.value || undefined) as ScenarioFilters["kind"] })}>
            <option value="">Все</option>
            <option value="call_intake">Приём вызова (оператор 112)</option>
            <option value="card_response">Реагирование на карточку (ДДС)</option>
          </select>
        </div>
        <div>
          <Label htmlFor="group">Группа происшествий</Label>
          <select id="group" className={selectClass} value={filters.group ?? ""} onChange={(e) => patch({ group: e.target.value || undefined })}>
            <option value="">Все</option>
            {tree.data?.groups.map((g) => (
              <option key={g.code} value={g.code}>
                {g.title}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="source">Источник</Label>
          <select id="source" className={selectClass} value={filters.source ?? ""} onChange={(e) => patch({ source: e.target.value || undefined })}>
            <option value="">Все</option>
            {options.data?.sources.map((s) => (
              <option key={s.code} value={s.code}>
                {s.title}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="status">Статус</Label>
          <select id="status" className={selectClass} value={filters.status ?? ""} onChange={(e) => patch({ status: (e.target.value || undefined) as ScenarioFilters["status"] })}>
            <option value="">Все</option>
            {options.data?.statuses.map((s) => (
              <option key={s.code} value={s.code}>
                {s.title}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="ticket">Билет</Label>
          <Input id="ticket" value={filters.ticket ?? ""} onChange={(e) => patch({ ticket: e.target.value || undefined })} placeholder="2-1" />
        </div>
      </div>

      {list.isPending ? (
        <LoadingState text="Загружаем сценарии…" />
      ) : list.isError ? (
        <ErrorState message={list.error.message} onRetry={() => void list.refetch()} />
      ) : list.data.items.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-muted-foreground">
            Ничего не найдено. Измените фильтры или сгенерируйте сценарий по фразе.
          </CardContent>
        </Card>
      ) : (
        <ScenarioTable items={list.data.items} />
      )}
    </div>
  );
}

function ScenarioTable({ items }: { items: ScenarioListItem[] }) {
  return (
    <>
      <p className="text-sm text-muted-foreground">
        {items.length} {plural(items.length, "сценарий", "сценария", "сценариев")}
      </p>
      <table className="hidden w-full text-sm md:table" aria-label="Список сценариев">
        <thead className="text-left text-xs text-muted-foreground">
          <tr className="border-b">
            <th className="py-2 pr-3 font-medium">Сценарий</th>
            <th className="py-2 pr-3 font-medium">Режим</th>
            <th className="py-2 pr-3 font-medium">Тип происшествия</th>
            <th className="py-2 pr-3 font-medium">Билет</th>
            <th className="py-2 pr-3 font-medium">Источник</th>
            <th className="py-2 pr-3 font-medium">Реплики</th>
            <th className="py-2 pr-3 font-medium">Статус</th>
            <th className="py-2 pr-3 font-medium">Обновлён</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.id} className="border-b last:border-0 hover:bg-accent/40" data-scenario={s.title}>
              <td className="py-2 pr-3">
                <Link to={`/teacher/scenarios/${s.id}`} className="font-medium hover:underline">
                  {s.title}
                </Link>
                <div className="text-xs text-muted-foreground">
                  {DIFFICULTY_SHORT[s.difficulty] ?? s.difficulty} · версия {s.current_version}
                </div>
              </td>
              <td className="py-2 pr-3">{MODE_TITLES[s.kind] ?? s.kind}</td>
              <td className="py-2 pr-3">
                <div>{s.incident_type_title ?? "—"}</div>
                <div className="text-xs text-muted-foreground">{s.incident_group_title ?? ""}</div>
              </td>
              <td className="py-2 pr-3 tabular-nums">{s.ticket_ref ?? "—"}</td>
              <td className="py-2 pr-3">{SOURCE_TITLES[s.source] ?? s.source}</td>
              <td className="py-2 pr-3 tabular-nums">
                {s.kind === "call_intake" ? <RepliesCell item={s} /> : "—"}
              </td>
              <td className="py-2 pr-3">
                <Badge tone={SCENARIO_STATUS_TONES[s.status]}>{SCENARIO_STATUS_TITLES[s.status] ?? s.status}</Badge>
              </td>
              <td className="py-2 pr-3 whitespace-nowrap text-muted-foreground">{formatDate(s.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="space-y-2 md:hidden" aria-label="Список сценариев">
        {items.map((s) => (
          <li key={s.id}>
            <Link to={`/teacher/scenarios/${s.id}`} className="block rounded-lg border p-3 hover:bg-accent/40">
              <div className="flex items-start justify-between gap-2">
                <span className="font-medium">{s.title}</span>
                <Badge tone={SCENARIO_STATUS_TONES[s.status]}>{SCENARIO_STATUS_TITLES[s.status] ?? s.status}</Badge>
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {MODE_TITLES[s.kind]} · {s.incident_type_title ?? "тип не задан"} · {SOURCE_TITLES[s.source] ?? s.source}
                {s.ticket_ref ? ` · билет ${s.ticket_ref}` : ""}
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}

function RepliesCell({ item }: { item: ScenarioListItem }) {
  return (
    <span title="утверждено / всего">
      {item.replies_approved}/{item.replies_total}
      {item.replies_pending > 0 && (
        <Badge tone="warning" className="ml-2">
          {item.replies_pending} на утверждение
        </Badge>
      )}
    </span>
  );
}

// ---------------------------------------------------------------- generation

function GenerateForm({ onClose }: { onClose: () => void }) {
  const options = useScenarioOptions();
  const tree = useClassifierTree();
  const generate = useGenerateScenario();
  const navigate = useNavigate();
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const [form, setForm] = useState<GenerateIn>({
    kind: "call_intake",
    phrase: "",
    incident_group: null,
    incident_type: null,
    difficulty: 2,
    persona: null,
    noise: null,
    both_kinds: false,
  });
  const patch = (next: Partial<GenerateIn>) => setForm((f) => ({ ...f, ...next }));

  const group = tree.data?.groups.find((g) => g.code === form.incident_group);
  const leaves = group ? flattenLeaves(group.children) : [];

  useEffect(() => {
    if (job.data?.status === "done" && job.data.result) {
      const result = job.data.result as { scenario_id?: string };
      if (result.scenario_id) void navigate(`/teacher/scenarios/${result.scenario_id}`);
    }
  }, [job.data, navigate]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const accepted = await generate.mutateAsync({ ...form, phrase: form.phrase.trim() });
    setJobId(accepted.job_id);
  };

  const running = Boolean(jobId) && job.data?.status !== "failed" && job.data?.status !== "done";

  return (
    <Card data-testid="generate-form">
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2">
            <Wand2 className="size-5" aria-hidden /> Генерация по фразе
          </CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            {options.data?.generation_method === "llm"
              ? "Языковая модель составит лист фактов, реплики и эталон по вашей фразе; проверка займёт до двух минут."
              : "Модель генерации не запущена: сценарий соберётся по шаблону из фразы, его нужно будет доработать вручную."}
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Скрыть
        </Button>
      </CardHeader>
      <CardContent>
        <form className="grid gap-4 md:grid-cols-3" onSubmit={(e) => void submit(e)}>
          <div className="md:col-span-3">
            <Label htmlFor="phrase">Что должно случиться</Label>
            <Input
              id="phrase"
              required
              minLength={3}
              maxLength={500}
              value={form.phrase}
              onChange={(e) => patch({ phrase: e.target.value })}
              placeholder="пожар в подземном паркинге, звонит ребёнок"
            />
          </div>
          <div>
            <Label htmlFor="gen-kind">Режим</Label>
            <select id="gen-kind" className={selectClass} value={form.kind} onChange={(e) => patch({ kind: e.target.value as GenerateIn["kind"] })}>
              <option value="call_intake">Приём вызова (оператор 112)</option>
              <option value="card_response">Реагирование на карточку (ДДС)</option>
            </select>
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.both_kinds ?? false} onChange={(e) => patch({ both_kinds: e.target.checked })} />
              Сразу оба режима
            </label>
          </div>
          <div>
            <Label htmlFor="gen-group">Группа происшествий</Label>
            <select
              id="gen-group"
              className={selectClass}
              value={form.incident_group ?? ""}
              onChange={(e) => patch({ incident_group: e.target.value || null, incident_type: null })}
            >
              <option value="">Подобрать по фразе</option>
              {tree.data?.groups.map((g) => (
                <option key={g.code} value={g.code}>
                  {g.title}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label htmlFor="gen-type">Тип из дерева</Label>
            <select
              id="gen-type"
              className={selectClass}
              value={form.incident_type ?? ""}
              onChange={(e) => patch({ incident_type: e.target.value || null })}
              disabled={!group}
            >
              <option value="">Подобрать по фразе</option>
              {leaves.map((leaf) => (
                <option key={leaf.code} value={leaf.code}>
                  {leaf.title}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label htmlFor="gen-difficulty">Сложность</Label>
            <select id="gen-difficulty" className={selectClass} value={form.difficulty ?? 2} onChange={(e) => patch({ difficulty: Number(e.target.value) })}>
              {[1, 2, 3].map((d) => (
                <option key={d} value={d}>
                  {d} — {DIFFICULTY_SHORT[d]}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label htmlFor="gen-persona">Персонаж заявителя</Label>
            <select id="gen-persona" className={selectClass} value={form.persona ?? ""} onChange={(e) => patch({ persona: e.target.value || null })} disabled={form.kind === "card_response" && !form.both_kinds}>
              <option value="">По ситуации</option>
              {options.data?.personas.map((p) => (
                <option key={p.code} value={p.code}>
                  {p.title}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label htmlFor="gen-noise">Шум на фоне</Label>
            <select id="gen-noise" className={selectClass} value={form.noise ?? ""} onChange={(e) => patch({ noise: e.target.value || null })} disabled={form.kind === "card_response" && !form.both_kinds}>
              <option value="">По ситуации</option>
              {options.data?.noises.map((n) => (
                <option key={n.code} value={n.code}>
                  {n.title}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-wrap items-center gap-3 md:col-span-3">
            <Button type="submit" disabled={generate.isPending || running}>
              {generate.isPending || running ? <Loader2 className="animate-spin" /> : <Sparkles />}
              Сгенерировать
            </Button>
            {generate.isError && <span className="text-sm text-destructive">{generate.error.message}</span>}
            {jobId && job.data && <JobProgress job={job.data} />}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

type TreeNode = { title: string; type_code?: string | null; children: TreeNode[] };

/** Leaves of the classifier tree as «sign1 → sign2 → sign3» options with the type code. */
function flattenLeaves(nodes: TreeNode[]): { code: string; title: string }[] {
  const out: { code: string; title: string }[] = [];
  const walk = (list: TreeNode[], prefix: string) => {
    for (const node of list) {
      const title = prefix ? `${prefix} → ${node.title}` : node.title;
      if (node.type_code && node.children.length === 0) out.push({ code: node.type_code, title });
      else walk(node.children, title);
    }
  };
  walk(nodes, "");
  return out;
}

export function JobProgress({ job }: { job: { status: string; progress: number; message?: string | null; error?: string | null } }) {
  if (job.status === "failed") {
    return (
      <span className="text-sm text-destructive" role="alert">
        Не получилось: {job.error ?? "неизвестная ошибка"}
      </span>
    );
  }
  if (job.status === "done") return <span className="text-sm text-success">Готово, открываем сценарий…</span>;
  return (
    <span className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
      <span className="h-2 w-40 overflow-hidden rounded bg-muted">
        <span className="block h-full bg-primary transition-all" style={{ width: `${Math.max(job.progress, 3)}%` }} />
      </span>
      {job.message ?? "В работе…"} ({job.progress}%)
    </span>
  );
}

// ---------------------------------------------------------------- methodical documents

function ReferenceDocs() {
  const docs = useReferenceDocs();
  const upload = useUploadReferenceDoc();
  const remove = useDeleteReferenceDoc();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookOpen className="size-5" aria-hidden /> Методические материалы
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          Документы (PDF, DOCX, TXT), которые генерация читает вместе с памяткой АРМ-112 и билетами.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
          <FileUp className="size-4" aria-hidden />
          <span className="underline">{upload.isPending ? "Загружаем…" : "Загрузить документ"}</span>
          <input
            type="file"
            accept=".pdf,.docx,.txt,.md"
            className="sr-only"
            disabled={upload.isPending}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) upload.mutate(file);
              e.target.value = "";
            }}
          />
        </label>
        {upload.isError && <p className="text-sm text-destructive">{upload.error.message}</p>}
        {docs.data && docs.data.length > 0 ? (
          <ul className="divide-y text-sm">
            {docs.data.map((d) => (
              <li key={d.name} className="flex items-center justify-between gap-3 py-2">
                <span>
                  {d.name}
                  <span className="text-muted-foreground">
                    {" "}
                    · {d.chunks} {plural(d.chunks, "фрагмент", "фрагмента", "фрагментов")}
                  </span>
                </span>
                <Button variant="ghost" size="sm" onClick={() => remove.mutate(d.name)} aria-label={`Удалить ${d.name}`}>
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">Пока только памятка АРМ-112 и билеты.</p>
        )}
      </CardContent>
    </Card>
  );
}
