import { describe, expect, it } from "vitest";

import type { ClassifierGroupOut } from "@/api/intake";
import { groupOf, levelsOf, typeOf } from "@/intake/classifier";
import { addressLine, EMPTY_ADDRESS, initialDraft } from "@/intake/draft";

const GROUPS: ClassifierGroupOut[] = [
  {
    code: "1",
    title: "Пожары и задымления",
    children: [
      {
        title: "жилой дом",
        type_code: null,
        final_title: null,
        flags: [],
        children: [
          {
            title: "квартира",
            type_code: null,
            final_title: null,
            flags: [],
            children: [
              { title: "открытое пламя", type_code: "1.5.1.1", final_title: "пожар: квартира", flags: ["threat"], children: [] },
              { title: "дым", type_code: "1.5.1.2", final_title: "задымление: квартира", flags: [], children: [] },
            ],
          },
        ],
      },
    ],
  },
  {
    code: "2",
    title: "ДТП",
    children: [
      {
        title: "ДТП",
        type_code: "2.1.0.0",
        final_title: "ДТП без пострадавших",
        flags: [],
        children: [{ title: "Транспорт легковой", type_code: "2.1.1.0", final_title: "ДТП легковой", flags: [], children: [] }],
      },
    ],
  },
];

describe("опросная карта", () => {
  it("строит ряды кнопок по выбранному пути", () => {
    const rows = levelsOf(GROUPS[0], ["жилой дом", "квартира"]);
    expect(rows.map((r) => r.map((n) => n.title))).toEqual([["жилой дом"], ["квартира"], ["открытое пламя", "дым"]]);
    expect(levelsOf(GROUPS[0], []).length).toBe(1);
  });

  it("тип — самый глубокий выбранный признак, который сам является типом", () => {
    expect(typeOf(GROUPS[0], ["жилой дом", "квартира"])).toBeNull();
    expect(typeOf(GROUPS[0], ["жилой дом", "квартира", "дым"])?.type_code).toBe("1.5.1.2");
    // «ДТП» — и тип, и папка: без уточнения остаётся «ДТП без пострадавших».
    expect(typeOf(GROUPS[1], ["ДТП"])?.type_code).toBe("2.1.0.0");
    expect(typeOf(GROUPS[1], ["ДТП", "Транспорт легковой"])?.type_code).toBe("2.1.1.0");
  });

  it("группа восстанавливается из кода типа или первого признака", () => {
    expect(groupOf(GROUPS, { signs_path: [], incident_type: "2.1.1.0" })?.code).toBe("2");
    expect(groupOf(GROUPS, { signs_path: ["жилой дом"], incident_type: null })?.code).toBe("1");
    expect(groupOf(GROUPS, { signs_path: [], incident_type: null })).toBeUndefined();
  });
});

describe("черновик карточки", () => {
  it("адрес в одну строку как в шапке карточки", () => {
    expect(addressLine({ ...EMPTY_ADDRESS, street: "улица Грина", house: "11", entrance: "2" })).toBe("Россия, Москва, улица Грина, 11, под. 2");
    expect(addressLine({ ...EMPTY_ADDRESS, region: "Московская область", city: "Красногорск", street: "улица Пионерская" })).toBe(
      "Россия, Московская область, Красногорск, улица Пионерская",
    );
    expect(addressLine({ ...EMPTY_ADDRESS, descriptive: "станция метро Арбатская" })).toBe("станция метро Арбатская");
  });

  it("берёт более свежую копию: локальную или серверную", () => {
    localStorage.setItem(
      "intake.draft.a1",
      JSON.stringify({ card: { description: "локально" }, updated_at: "2026-09-16T10:00:00.000Z", manual_services: ["102"] }),
    );
    const older = initialDraft("a1", { description: "с сервера", updated_at: "2026-09-16T09:00:00.000Z" });
    expect(older.card.description).toBe("локально");
    const newer = initialDraft("a1", { description: "с сервера", updated_at: "2026-09-16T11:00:00.000Z" });
    expect(newer.card.description).toBe("с сервера");
    expect(newer.manual_services).toEqual(["102"]);
    expect(newer.card.address).toEqual(EMPTY_ADDRESS);
    localStorage.clear();
    expect(initialDraft("a1", null).card.description).toBe("");
  });
});
