import { Plus, Save, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import {
  useAddPhone,
  useDeletePhone,
  usePhoneDirectory,
  useUpdatePhone,
  type PhoneDirectoryOut,
  type PhoneEntryOut,
} from "@/api/phones";
import { useServices } from "@/api/teacher";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

type Roles = PhoneDirectoryOut["roles"];

/**
 * «Справочник телефонов» (#263, рекомендации заказчика п. 8): the numbers a trainee dials on
 * the virtual phone of the card — up to ten per dispatch service, the customer's four roles.
 * One directory for all teachers.
 */
/** City dispatch services with emergency squads and the district councils: whose ДДС a
 * trainee is (the customer's four roles of the phone fit them). */
const DDS_SERVICES = new Set([
  "gkh",
  "moek",
  "mosgaz",
  "mosvodokanal",
  "gormost",
  "moslift",
  "oek",
  "moesk",
  "mosvodostok",
  "moscollector",
  "territorial_oiv",
  "territorial_oiv_tinao",
]);

export function TeacherPhonesPage() {
  const directory = usePhoneDirectory();
  const services = useServices();
  const [picked, setPicked] = useState<string | null>(null);

  if (directory.isPending || services.isPending) return <LoadingState text="Загружаем справочник…" />;
  if (directory.isError)
    return <ErrorState message={directory.error.message} onRetry={() => void directory.refetch()} />;
  if (services.isError)
    return <ErrorState message={services.error.message} onRetry={() => void services.refetch()} />;

  // Only the dispatch services a trainee works for, not the emergency lines (101–104) and
  // the rest of the classifier: every service got default numbers, so «has numbers» was all.
  const list = services.data.filter((s) => DDS_SERVICES.has(s.code));
  const service = picked ?? list[0]?.code ?? "";
  const entries = directory.data.entries.filter((e) => e.service_code === service);
  const max = directory.data.max_per_service;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Справочник телефонов</h1>
        <p className="text-sm text-muted-foreground">
          Номера, которые обучаемый набирает на виртуальном телефоне карточки: до {max} на службу.
          Справочник один для всех преподавателей.
        </p>
      </div>

      <label className="flex max-w-md flex-col gap-1.5 text-sm font-medium">
        Служба
        <Select value={service} onChange={(e) => setPicked(e.target.value)} aria-label="Служба">
          {list.map((s) => (
            <option key={s.code} value={s.code}>
              {s.short_title}
            </option>
          ))}
        </Select>
      </label>

      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="w-full min-w-[640px] text-sm" aria-label="Номера службы">
          <thead className="text-left text-xs text-muted-foreground">
            <tr className="border-b">
              <th className="py-2 pl-3 pr-3 font-medium">Должность</th>
              <th className="py-2 pr-3 font-medium">ФИО</th>
              <th className="w-28 py-2 pr-3 font-medium">Номер</th>
              <th className="w-56 py-2 pr-3 font-medium" />
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <EntryRow key={e.id} entry={e} roles={directory.data.roles} />
            ))}
            {entries.length === 0 && (
              <tr>
                <td colSpan={4} className="py-6 text-center text-muted-foreground">
                  У службы пока нет номеров: добавьте первый ниже.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {entries.length < max ? (
        <AddForm key={service} service={service} roles={directory.data.roles} />
      ) : (
        <p className="text-sm text-muted-foreground">
          У службы уже {max} номеров — больше нельзя. Измените или удалите один из них.
        </p>
      )}
    </div>
  );
}

function RoleSelect({
  value,
  roles,
  onChange,
  label,
}: {
  value: string;
  roles: Roles;
  onChange: (role: string) => void;
  label: string;
}) {
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      {roles.map((r) => (
        <option key={r.code} value={r.code}>
          {r.title}
        </option>
      ))}
    </Select>
  );
}

function EntryRow({ entry, roles }: { entry: PhoneEntryOut; roles: Roles }) {
  const [role, setRole] = useState(entry.role);
  const [name, setName] = useState(entry.name);
  const [number, setNumber] = useState(entry.number);
  const update = useUpdatePhone();
  const remove = useDeletePhone();
  const changed = role !== entry.role || name.trim() !== entry.name || number !== entry.number;
  const busy = update.isPending || remove.isPending;
  const error = update.error ?? remove.error;

  return (
    <tr className="border-b align-top last:border-0" data-number={entry.number}>
      <td className="py-2 pl-3 pr-3">
        <RoleSelect value={role} roles={roles} onChange={setRole} label={`Должность: ${entry.name}`} />
      </td>
      <td className="py-2 pr-3">
        <Input value={name} onChange={(e) => setName(e.target.value)} aria-label={`ФИО: ${entry.name}`} />
        {error && (
          <p className="mt-1 text-xs text-destructive" role="alert">
            {error.message}
          </p>
        )}
      </td>
      <td className="py-2 pr-3">
        <Input
          value={number}
          onChange={(e) => setNumber(e.target.value.replace(/\D/g, "").slice(0, 4))}
          inputMode="numeric"
          aria-label={`Номер: ${entry.name}`}
          className="font-mono"
        />
      </td>
      <td className="py-2 pr-3">
        <div className="flex gap-2">
          <Button
            size="sm"
            disabled={!changed || busy || !name.trim()}
            onClick={() => update.mutate({ id: entry.id, role, name: name.trim(), number })}
          >
            <Save /> Сохранить
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => {
              if (window.confirm(`Удалить номер ${entry.number} (${entry.name})?`)) remove.mutate(entry.id);
            }}
          >
            <Trash2 /> Удалить
          </Button>
        </div>
      </td>
    </tr>
  );
}

function AddForm({ service, roles }: { service: string; roles: Roles }) {
  const [role, setRole] = useState(roles[0]?.code ?? "");
  const [name, setName] = useState("");
  const [number, setNumber] = useState("");
  const add = useAddPhone();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    add.mutate(
      { service_code: service, role, name: name.trim(), number },
      {
        onSuccess: () => {
          setName("");
          setNumber("");
        },
      },
    );
  };

  return (
    <form onSubmit={submit} className="space-y-2 rounded-lg border bg-card p-3" aria-label="Новый номер">
      <div className="grid gap-2 md:grid-cols-[1fr_1fr_7rem_auto]">
        <RoleSelect value={role} roles={roles} onChange={setRole} label="Должность нового номера" />
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="ФИО"
          aria-label="ФИО нового номера"
        />
        <Input
          value={number}
          onChange={(e) => setNumber(e.target.value.replace(/\D/g, "").slice(0, 4))}
          placeholder="2105"
          inputMode="numeric"
          aria-label="Номер нового номера"
          className="font-mono"
        />
        <Button type="submit" disabled={add.isPending || !name.trim() || number.length !== 4}>
          <Plus /> Добавить номер
        </Button>
      </div>
      {add.isError && (
        <p className="text-xs text-destructive" role="alert">
          {add.error.message}
        </p>
      )}
    </form>
  );
}
