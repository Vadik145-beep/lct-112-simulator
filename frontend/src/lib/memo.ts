/** The page of the memo «Работа на АРМ-112» a memo reference names: «стр. 31, пример 6» → 31. */
export function memoPage(ref: string): number | null {
  const match = /стр\.?\s*(\d+)/i.exec(ref);
  return match ? Number(match[1]) : null;
}

/** The memo opened at the page of the reference (20.10.2026: the link searched the error's
 * title in the reference and found nothing). */
export function memoLink(referenceBase: string, ref: string): string {
  const page = memoPage(ref);
  return `${referenceBase}/materials/memo${page ? `#page-${page}` : ""}`;
}
