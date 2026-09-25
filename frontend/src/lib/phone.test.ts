import { describe, expect, it } from "vitest";

import { formatPhone } from "./phone";

describe("formatPhone", () => {
  it("groups a stored number", () => {
    expect(formatPhone("79220000001")).toBe("+7 922 000-00-01");
  });

  it("leaves anything else as is", () => {
    expect(formatPhone("112")).toBe("112");
  });
});
