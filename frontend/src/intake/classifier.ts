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

export interface TypeHit {
  group: ClassifierGroupOut;
  path: string[];
  node: ClassifierNode;
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/ё/g, "е");
}

/** Every type of the classifier as a flat list with its path of signs. */
export function flattenTypes(groups: ClassifierGroupOut[]): TypeHit[] {
  const out: TypeHit[] = [];
  const walk = (group: ClassifierGroupOut, nodes: ClassifierNode[], path: string[]) => {
    for (const node of nodes) {
      const next = [...path, node.title];
      if (node.type_code) out.push({ group, path: next, node });
      if (node.children.length > 0) walk(group, node.children, next);
    }
  };
  for (const group of groups) walk(group, group.children, []);
  return out;
}

/** Types whose final title or signs contain every word of the query, in any order and form
 * («тран» finds «Транспорт», «пожар кварт» finds «пожар: квартира»), as the search line
 * «что случилось?» of the live АРМ-112 works. The group title is left out: «пожар» would
 * otherwise match every type of «Пожары и задымления». */
export function searchTypes(all: TypeHit[], query: string, limit = 12): TypeHit[] {
  const words = normalize(query).split(/\s+/).filter((w) => w.length >= 2);
  if (words.length === 0) return [];
  const hits: TypeHit[] = [];
  for (const hit of all) {
    const haystack = normalize([hit.node.final_title ?? "", ...hit.path].join(" "));
    if (words.every((w) => haystack.includes(w))) {
      hits.push(hit);
      if (hits.length >= limit) break;
    }
  }
  return hits;
}
