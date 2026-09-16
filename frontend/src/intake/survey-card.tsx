import { X } from "lucide-react";
import { useMemo, useState } from "react";

import type { ClassifierGroupOut, ClassifierNode } from "@/api/intake";
import { groupOf, levelsOf, typeOf, type Selection } from "@/intake/classifier";
import { cn } from "@/lib/utils";

export function SurveyCard({
  groups,
  selection,
  number,
  onChange,
  disabled,
}: {
  groups: ClassifierGroupOut[];
  selection: Selection;
  number: string;
  onChange: (selection: Selection, node: ClassifierNode | null) => void;
  disabled?: boolean;
}) {
  const [picking, setPicking] = useState(false);
  // A group picked with no sign chosen yet cannot be derived from the selection.
  const [pickedGroup, setPickedGroup] = useState<string | null>(null);
  const group = useMemo(
    () => groupOf(groups, selection) ?? groups.find((g) => g.code === pickedGroup && selection.signs_path.length === 0),
    [groups, selection, pickedGroup],
  );
  const rows = useMemo(() => levelsOf(group, selection.signs_path), [group, selection.signs_path]);

  const choose = (g: ClassifierGroupOut, path: string[]) => {
    const node = typeOf(g, path);
    onChange({ signs_path: path, incident_type: node?.type_code ?? null }, node);
  };

  const pickGroup = (g: ClassifierGroupOut) => {
    setPicking(false);
    setPickedGroup(g.code);
    choose(g, []);
  };

  const pickSign = (level: number, title: string) => {
    if (!group) return;
    const path = selection.signs_path.slice(0, level);
    const unselect = selection.signs_path[level] === title;
    choose(group, unselect ? path : [...path, title]);
  };

  const clear = () => {
    setPickedGroup(null);
    onChange({ signs_path: [], incident_type: null }, null);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1" data-testid="survey-card">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setPicking((v) => !v)}
        aria-expanded={picking}
        className="bg-[var(--arm-panel)] px-3 py-2 text-left text-lg text-[var(--arm-text-muted)] hover:text-[var(--arm-text)] disabled:cursor-not-allowed"
      >
        добавить тип происшествия
      </button>
      {picking && (
        <div className="arm-scroll max-h-48 overflow-y-auto bg-white p-2" role="listbox" aria-label="Группа происшествия">
          <div className="flex flex-wrap gap-1">
            {groups.map((g) => (
              <SignButton key={g.code} active={group?.code === g.code} onClick={() => pickGroup(g)}>
                <span className="text-[var(--arm-text-muted)]">{g.code}.</span> {g.title}
              </SignButton>
            ))}
          </div>
        </div>
      )}
      {group && (
        <>
          <div className="flex gap-1 bg-[var(--arm-panel)] px-2 py-1">
            <span className="border border-[#a9adb2] bg-white px-2 py-0.5 text-xs">Происшествие {number}</span>
          </div>
          <div className="flex min-h-0 flex-1 flex-col bg-white" aria-label="Опросная карта">
            <div className="flex items-center justify-between bg-[var(--arm-dark)] px-3 py-1.5 text-sm font-semibold text-[var(--arm-on-dark)]">
              <span className="underline decoration-[var(--arm-on-dark-muted)] underline-offset-4">Происшествие {number}</span>
              <button type="button" aria-label="Убрать тип происшествия" onClick={clear} disabled={disabled} className="rounded-sm p-0.5 hover:bg-[var(--arm-dark-2)]">
                <X className="size-4" />
              </button>
            </div>
            <div className="arm-scroll flex-1 overflow-y-auto">
              <table className="w-full text-sm">
                <tbody>
                  {rows.map((nodes, level) => (
                    <tr key={level} className="align-top">
                      <th scope="row" className="w-40 px-3 py-2 text-left text-xs font-normal text-[var(--arm-text-muted)]">
                        {level === 0 ? group.title : selection.signs_path[level - 1]}
                      </th>
                      <td className="px-2 py-1.5">
                        <div className="flex flex-wrap gap-1" role="group" aria-label={level === 0 ? group.title : selection.signs_path[level - 1]}>
                          {nodes.map((n, i) => (
                            <SignButton
                              key={`${n.title}-${i}`}
                              active={selection.signs_path[level] === n.title}
                              disabled={disabled}
                              title={n.final_title ?? undefined}
                              onClick={() => pickSign(level, n.title)}
                            >
                              {n.title}
                            </SignButton>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="border-t border-[#dcdedf] px-3 py-1.5 text-xs">
              <span className="text-[var(--arm-text-muted)]">Класс.: </span>
              <b data-testid="survey-type">{typeOf(group, selection.signs_path)?.final_title ?? "тип не выбран"}</b>
              {selection.incident_type && <span className="ml-2 text-[var(--arm-text-muted)]">{selection.incident_type}</span>}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export function SignButton({
  active,
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={cn(
        "inline-flex min-h-7 items-center rounded-sm border px-2 py-0.5 text-left text-xs leading-tight transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--arm-blue)] disabled:cursor-not-allowed disabled:opacity-60",
        active
          ? "border-[var(--arm-blue)] bg-[var(--arm-blue)] font-semibold text-white"
          : "border-[#a9adb2] bg-white text-[var(--arm-text)] hover:border-[var(--arm-blue)]",
        className,
      )}
      {...props}
    />
  );
}
