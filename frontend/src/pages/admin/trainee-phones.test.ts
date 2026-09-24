import { describe, expect, it } from "vitest";

import { phonesToText, textToPhones } from "./trainee-phones";

describe("trainee phones", () => {
  it("reads one «login = phone» per line and skips the rest", () => {
    expect(
      textToPhones(
        "student1 = 8 922 000-00-01\n\n  student2=+79220000002 \nbroken\n= 8922",
      ),
    ).toEqual({
      student1: "8 922 000-00-01",
      student2: "+79220000002",
    });
  });

  it("writes the table back as lines", () => {
    expect(
      phonesToText({ student1: "79220000001", student2: "79220000002" }),
    ).toBe("student1 = 79220000001\nstudent2 = 79220000002");
    expect(phonesToText(undefined)).toBe("");
  });
});
