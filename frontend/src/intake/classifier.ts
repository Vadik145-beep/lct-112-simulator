import type { ClassifierGroupOut, ClassifierNode } from "@/api/intake";

export interface Selection {
  signs_path: string[];
  incident_type: string | null;
}

/** Levels of the survey card: the group's first-level signs, then the children of each
 * chosen sign (classifier columns G → H → I). */
export function levelsOf(group: ClassifierGroupOut | undefined, path: string[]): ClassifierNode[][] {
  if (!group) return [];
  const rows: ClassifierNode[][] = [group.children];
  let level = group.children;
  for (const title of path) {
    const node = level.find((n) => n.title === title);
    if (!node || node.children.length === 0) break;
    rows.push(node.children);
    level = node.children;
  }
  return rows;
}

/** The type of the deepest chosen sign that is a type itself (a sign may be both a type and
 * a folder: «ДТП» is «ДТП без пострадавших» and also opens «Транспорт легковой»). */
export function typeOf(group: ClassifierGroupOut | undefined, path: string[]): ClassifierNode | null {
  if (!group) return null;
  let level = group.children;
  let found: ClassifierNode | null = null;
  for (const title of path) {
    const node = level.find((n) => n.title === title);
    if (!node) break;
    if (node.type_code) found = node;
    level = node.children;
  }
  return found;
}

export function groupOf(groups: ClassifierGroupOut[], selection: Selection): ClassifierGroupOut | undefined {
  if (selection.incident_type) {
    const code = selection.incident_type.split(".")[0];
    return groups.find((g) => g.code === code);
  }
  const first = selection.signs_path[0];
  if (!first) return undefined;
  return groups.find((g) => g.children.some((n) => n.title === first));
}
