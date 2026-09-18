import { BarChart3, Pencil, Plus, Users } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { useCreateGroup, useGroups, useStudents, useUpdateGroup, type GroupOut, type StudentOut } from "@/api/teacher";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { plural } from "@/teacher/labels";

/** «Группы»: the teacher's groups with their members; create and edit inline. */
export function TeacherGroupsPage() {
  const groups = useGroups();
  const students = useStudents();
  const [editing, setEditing] = useState<"new" | string | null>(null);

  if (groups.isPending || students.isPending) return <LoadingState text="Загружаем группы…" />;
  if (groups.isError) return <ErrorState message={groups.error.message} onRetry={() => void groups.refetch()} />;
  if (students.isError) return <ErrorState message={students.error.message} onRetry={() => void students.refetch()} />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Группы</h1>
          <p className="text-sm text-muted-foreground">Занятие назначается группе; состав можно менять в любой момент.</p>
        </div>
        <Button onClick={() => setEditing("new")} disabled={editing === "new"}>
          <Plus /> Создать группу
        </Button>
      </div>

      {editing === "new" && <GroupForm students={students.data} onDone={() => setEditing(null)} />}

      {groups.data.length === 0 && editing !== "new" ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
            <Users className="size-8" aria-hidden />
            <p>Групп пока нет. Создайте группу и добавьте в неё обучающихся.</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {groups.data.map((g) =>
            editing === g.id ? (
              <GroupForm key={g.id} group={g} students={students.data} onDone={() => setEditing(null)} />
            ) : (
              <Card key={g.id} data-group={g.title}>
                <CardHeader className="flex-row items-start justify-between space-y-0">
                  <div>
                    <CardTitle>{g.title}</CardTitle>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {g.members.length} {plural(g.members.length, "обучающийся", "обучающихся", "обучающихся")}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" asChild>
                      <Link to={`/teacher/analytics?group=${g.id}`} aria-label={`Аналитика группы ${g.title}`}>
                        <BarChart3 /> Аналитика
                      </Link>
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => setEditing(g.id)} aria-label={`Изменить группу ${g.title}`}>
                      <Pencil /> Изменить
                    </Button>
                  </div>
                </CardHeader>
                <CardContent>
                  {g.members.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Состав пуст.</p>
                  ) : (
                    <ul className="divide-y text-sm">
                      {g.members.map((m) => (
                        <li key={m.id} className="flex justify-between gap-2 py-1">
                          <span>{m.full_name}</span>
                          <span className="text-muted-foreground">{m.login}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            ),
          )}
        </div>
      )}
    </div>
  );
}

function GroupForm({ group, students, onDone }: { group?: GroupOut; students: StudentOut[]; onDone: () => void }) {
  const create = useCreateGroup();
  const update = useUpdateGroup(group?.id ?? "");
  const mutation = group ? update : create;
  const [title, setTitle] = useState(group?.title ?? "");
  const [selected, setSelected] = useState<Set<string>>(() => new Set(group?.members.map((m) => m.id) ?? []));

  function submit(e: FormEvent) {
    e.preventDefault();
    mutation.mutate({ title: title.trim(), student_ids: [...selected] }, { onSuccess: onDone });
  }

  return (
    <Card className="border-primary/40 md:col-span-2">
      <form onSubmit={submit} aria-label={group ? `Изменение группы ${group.title}` : "Новая группа"}>
        <CardHeader>
          <CardTitle>{group ? "Изменить группу" : "Новая группа"}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="max-w-md space-y-1.5">
            <Label htmlFor="group-title">Название</Label>
            <Input id="group-title" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Например: Учебная-3" />
          </div>
          <fieldset>
            <legend className="mb-2 text-sm font-medium">
              Состав · выбрано {selected.size}
            </legend>
            <div className="grid max-h-72 gap-1 overflow-y-auto rounded-md border p-2 sm:grid-cols-2 lg:grid-cols-3">
              {students.map((s) => (
                <label key={s.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={selected.has(s.id)}
                    onChange={(e) =>
                      setSelected((prev) => {
                        const next = new Set(prev);
                        if (e.target.checked) next.add(s.id);
                        else next.delete(s.id);
                        return next;
                      })
                    }
                  />
                  <span>
                    {s.full_name} <span className="text-muted-foreground">· {s.login}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          {mutation.isError && <ErrorState message={mutation.error.message} />}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "Сохраняем…" : group ? "Сохранить группу" : "Создать группу"}
            </Button>
            <Button type="button" variant="outline" onClick={onDone}>
              Отмена
            </Button>
          </div>
        </CardContent>
      </form>
    </Card>
  );
}
