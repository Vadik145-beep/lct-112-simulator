import { describe, expect, it } from "vitest";

import type { ServiceStatusOut } from "@/api/training";
import { describeCorrection, serviceCorrection } from "@/emulator/flag-field-model";

const services: ServiceStatusOut[] = [
  { code: "moek", title: "МОЭК", short_title: "МОЭК", status: "added", status_title: "Добавлена", at: null, is_own: true },
  { code: "moslift", title: "Мослифт", short_title: "Мослифт", status: "received", status_title: "Получена службой", at: null, is_own: false },
];

describe("flag corrections", () => {
  it("encodes an extra service as «-code» and a missing one as «+code»", () => {
    expect(serviceCorrection("extra", "moslift")).toBe("-moslift");
    expect(serviceCorrection("missing", " 101 ")).toBe("+101");
  });

  it("describes a correction for the mark under the field", () => {
    expect(describeCorrection("services", "-moslift", services)).toBe("лишняя служба: Мослифт");
    expect(describeCorrection("services", "+101", services)).toBe("не хватает службы: 101");
    expect(describeCorrection("flags.injured", "false", services)).toBe("пострадавших нет");
    expect(describeCorrection("address.house", "17", services)).toBe("верно: 17");
  });
});
