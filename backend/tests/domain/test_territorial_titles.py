"""Names of the territorial ДДС tab derived from the card address (present.territorial_titles)."""

from app.training.present import territorial_titles

FALLBACK = ("Территориальные ОИВ (управы, префектуры)", "Управа")


def test_district_forms_are_kept_readable() -> None:
    assert territorial_titles({"okrug": "ЮЗАО", "district": "Ломоносовский район"}, *FALLBACK) == (
        "ДДС управы: Ломоносовский район, префектура ЮЗАО",
        "Упр. Ломоносовский район",
    )
    assert territorial_titles({"okrug": "СЗАО", "district": "район Щукино"}, *FALLBACK) == (
        "ДДС управы: район Щукино, префектура СЗАО",
        "Упр. район Щукино",
    )
    assert territorial_titles({"district": "Вешняки"}, *FALLBACK) == (
        "ДДС управы: Вешняки",
        "Упр. района Вешняки",
    )


def test_without_a_district_the_abstract_service_stays() -> None:
    assert territorial_titles({}, *FALLBACK) == FALLBACK
    assert territorial_titles({"okrug": "ЦАО"}, *FALLBACK) == FALLBACK
