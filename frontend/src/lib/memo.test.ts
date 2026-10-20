import { describe, expect, it } from "vitest";

import { memoLink, memoPage } from "@/lib/memo";

describe("memo links", () => {
  it("opens the memo at the first page the reference names", () => {
    expect(memoPage("стр. 31, пример 6")).toBe(31);
    expect(memoPage("стр. 22, 31 (пример 6)")).toBe(22);
    expect(memoLink("/student/reference", "стр. 21, 26 (своевременность)")).toBe(
      "/student/reference/materials/memo#page-21",
    );
    expect(memoLink("/teacher/reference", "раздел без страницы")).toBe("/teacher/reference/materials/memo");
  });
});
