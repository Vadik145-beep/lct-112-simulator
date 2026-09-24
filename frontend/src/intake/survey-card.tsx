import { X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { ClassifierGroupOut, ClassifierNode } from "@/api/intake";
import { flattenTypes, groupOf, levelsOf, searchTypes, typeOf, type Selection } from "@/intake/classifier";
import { cn } from "@/lib/utils";

export function SurveyCard({
  groups,
  selection,
  onChange,
  disabled,
}: {
  groups: ClassifierGroupOut[];
  selection: Selection;
  onChange: (selection: Selection, node: ClassifierNode | null) => void;
  disabled?: boolean;
}) {
  const [picking, setPicking] = useState(false);
  // «что случилось?» — the search line of the live АРМ-112: a part of a word finds the type
  // and fills the survey card with its signs.
  const [query, setQuery] = useState("");
  const allTypes = useMemo(() => flattenTypes(groups), [groups]);
  const hits = useMemo(() => searchTypes(allTypes, query), [allTypes, query]);
  // A group picked with no sign chosen yet cannot be derived from the selection.
  const [pickedGroup, setPickedGroup] = useState<string | null>(null);
  const group = useMemo(
    () => groupOf(groups, selection) ?? groups.find((g) => g.code === pickedGroup && selection.signs_path.length === 0),
    [groups, selection, pickedGroup],
  );
  const rows = useMemo(() => levelsOf(group, selection.signs_path), [group, selection.signs_path]);
  // The next row of buttons appears below the fold of the panel; bring it into view so the
  // operator sees what to choose next (docs/BUGS.md, «Опросная карта»).
  const lastRow = useRef<HTMLTableRowElement | null>(null);
  useEffect(() => {
    lastRow.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [rows.length]);
  const chosen = typeOf(group, selection.signs_path);
  const pending = selection.signs_path.length > 0 ? selection.signs_path[selection.signs_path.length - 1] : group?.title;

  const choose = (g: ClassifierGroupOut, path: string[]) => {
    const node = typeOf(g, path);
    onChange({ signs_path: path, incident_type: node?.type_code ?? null }, node);
  };

  const pickGroup = (g: ClassifierGroupOut) => {
    setPicking(false);
    setPickedGroup(g.code);
    choose(g, []);
  };

  const pickHit = (hit: (typeof hits)[number]) => {
    setQuery("");
    setPicking(false);
    setPickedGroup(hit.group.code);
    choose(hit.group, hit.path);
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
      <div className="relative flex items-center gap-2 bg-[var(--arm-panel)] px-3 py-1.5">
        <input
          aria-label="Что случилось?"
          placeholder={group ? "добавить тип происшествия" : "что случилось?"}
          value={query}
          disabled={disabled}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            const first = hits[0];
            if (e.key === "Enter" && first) {
              e.preventDefault();
              pickHit(first);
            } else if (e.key === "Escape") setQuery("");
          }}
          className="h-8 flex-1 border-b border-[#a9adb2] bg-transparent text-lg placeholder:text-[var(--arm-text-muted)] focus:border-[var(--arm-blue)] focus:outline-none disabled:cursor-not-allowed"
        />
        <button
          type="button"
          disabled={disabled}
          onClick={() => setPicking((v) => !v)}
          aria-expanded={picking}
          className="text-xs text-[var(--arm-text-muted)] hover:text-[var(--arm-text)] disabled:cursor-not-allowed"
        >
          по группам
        </button>
        {query.trim().length >= 2 && (
          <ul
            className="arm-scroll absolute top-full right-0 left-0 z-10 max-h-56 overflow-y-auto border border-[#a9adb2] bg-white text-sm shadow-lg"
            role="listbox"
            aria-label="Найденные типы происшествий"
          >
            {hits.length === 0 && <li className="px-3 py-2 text-xs text-[var(--arm-text-muted)]">Ничего не найдено — попробуйте часть слова или синоним.</li>}
            {hits.map((h) => (
              <li key={h.node.type_code}>
                <button type="button" onClick={() => pickHit(h)} className="flex w-full flex-col px-3 py-1.5 text-left hover:bg-[#eaf3fc]">
                  <span className="font-medium">{h.node.final_title}</span>
                  <span className="text-xs text-[var(--arm-text-muted)]">
                    {h.group.title} → {h.path.join(" → ")}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
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
            <span className="border border-[#a9adb2] bg-white px-2 py-0.5 text-xs">{group.title}</span>
          </div>
          <div className="flex min-h-0 flex-1 flex-col bg-white" aria-label="Опросная карта">
            <div className="flex items-center justify-between bg-[var(--arm-dark)] px-3 py-1.5 text-sm font-semibold text-[var(--arm-on-dark)]">
              <span className="underline decoration-[var(--arm-on-dark-muted)] underline-offset-4">{group.title}</span>
              <button type="button" aria-label="Убрать тип происшествия" onClick={clear} disabled={disabled} className="rounded-sm p-0.5 hover:bg-[var(--arm-dark-2)]">
                <X className="size-4" />
              </button>
            </div>
            <div className="arm-scroll flex-1 overflow-y-auto">
              <table className="w-full text-sm">
                <tbody>
                  {rows.map((nodes, level) => (
                    <tr key={level} className="align-top" ref={level === rows.length - 1 ? lastRow : undefined}>
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
              {chosen ? (
                <b data-testid="survey-type">{chosen.final_title}</b>
              ) : (
                <span data-testid="survey-type" className="text-[var(--arm-text-muted)]">
                  уточните «{pending}» — выберите вариант в последней строке
                </span>
              )}
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
        "inline-flex min-h-8 items-center rounded-sm border px-3 py-1 text-left text-sm leading-tight transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--arm-blue)] disabled:cursor-not-allowed disabled:opacity-60",
        active
          ? "border-[var(--arm-blue)] bg-[var(--arm-blue)] font-semibold text-white"
          : "border-[#a9adb2] bg-white text-[var(--arm-text)] hover:border-[var(--arm-blue)]",
        className,
      )}
      {...props}
    />
  );
}
