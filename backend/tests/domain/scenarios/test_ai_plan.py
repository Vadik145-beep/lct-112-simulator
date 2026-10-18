"""The next card of a trainee in a lesson where the cards are written for each trainee
(issue #265): what the difficulty takes, the weakest group first, staying in the group of a
failed card, the missed mistake again, two trainees getting different cards."""

from __future__ import annotations

from app.domain.scenarios import ai_plan
from app.domain.scenarios.ai_plan import EarlierCard, PlanInput, plan_next
from app.domain.scenarios.template import ServiceInfo
from app.domain.services import resolve_services

CATALOGUE = {
    "gkh": ServiceInfo("gkh", True, "Диспетчерская ЖКХ"),
    "moslift": ServiceInfo("moslift", True, "Мослифт"),
    "101": ServiceInfo("101", False, "Служба 101"),
}


def _spec(type_rows: list[dict], **overrides) -> PlanInput:
    base = {
        "difficulty": 2,
        "seq": 1,
        "trainee_key": "lesson-1|trainee-a",
        "type_rows": type_rows,
        "catalogue": CATALOGUE,
        "group_titles": {"14": "Аварии в городском хозяйстве", "1": "Пожары"},
        "ratings": {},
    }
    base.update(overrides)
    return PlanInput(**base)


def _group(code: str) -> str:
    return code.split(".")[0]


def test_difficulty_takes_its_kind_of_incident(type_rows: list[dict]) -> None:
    rows = {str(r["code"]): r for r in type_rows}
    for difficulty in (1, 2, 3):
        for seq in range(1, 6):
            plan = plan_next(_spec(type_rows, difficulty=difficulty, seq=seq))
            assert plan.request.kind == "card_response"
            assert plan.request.difficulty == difficulty
            code = str(plan.request.incident_type)
            assert _group(code) in ai_plan.EVERYDAY_GROUPS[difficulty]
            lo, hi = ai_plan.SERVICES_BY_DIFFICULTY[difficulty]
            services = resolve_services(rows[code].get("service_rules") or [], [])
            assert lo <= len(services) <= hi
            assert plan.request.phrase == rows[code]["final_title"]


def test_weakest_group_comes_first(type_rows: list[dict]) -> None:
    groups = ai_plan.EVERYDAY_GROUPS[2]
    ratings = dict.fromkeys(groups, 1500.0) | {"15": 1210.0}
    plan = plan_next(_spec(type_rows, ratings=ratings))
    assert plan.request.incident_group == "15"
    assert "самый низкий рейтинг" in plan.reasons[0]


def test_failed_card_keeps_the_group_and_plants_the_missed_mistake(type_rows: list[dict]) -> None:
    first = plan_next(_spec(type_rows))
    earlier = EarlierCard(
        incident_type=str(first.request.incident_type),
        total=55,
        passed=False,
        data_check=[{"field": "address.house", "title": "Дом", "verdict": "missed"}],
    )
    second = plan_next(_spec(type_rows, seq=2, earlier=[earlier]))
    assert second.request.incident_group == first.request.incident_group
    assert second.prefer[0] == "house"
    assert any("«Дом»" in r for r in second.reasons)


def test_passed_card_moves_on_to_another_group(type_rows: list[dict]) -> None:
    first = plan_next(_spec(type_rows))
    earlier = EarlierCard(
        incident_type=str(first.request.incident_type),
        total=95,
        passed=True,
        data_check=[{"field": "flags.injured", "title": "Пострадавшие", "verdict": "found"}],
    )
    second = plan_next(_spec(type_rows, seq=2, earlier=[earlier]))
    assert second.request.incident_group != first.request.incident_group
    # A kind the trainee has already met goes last.
    assert "injured" not in second.prefer


def test_two_trainees_get_different_cards(type_rows: list[dict]) -> None:
    a = plan_next(_spec(type_rows, trainee_key="lesson-1|a"))
    b = plan_next(
        _spec(type_rows, trainee_key="lesson-1|b", taken_types=[str(a.request.incident_type)])
    )
    assert b.request.incident_type != a.request.incident_type


def test_lesson_groups_win_over_the_difficulty(type_rows: list[dict]) -> None:
    plan = plan_next(_spec(type_rows, difficulty=1, allowed_groups=["1"]))
    assert plan.request.incident_group == "1"


def _rows_by_code(type_rows: list[dict]) -> dict[str, dict]:
    return {str(r["code"]): r for r in type_rows}


def test_cards_are_for_the_trainees_own_service(type_rows: list[dict]) -> None:
    rows = _rows_by_code(type_rows)
    for difficulty in (1, 2, 3):
        for seq in (1, 2, 4, 5):
            plan = plan_next(_spec(type_rows, difficulty=difficulty, seq=seq, own_service="gkh"))
            assert plan.request.service == "gkh"
            assert not plan.request.wrong_service
            row = rows[str(plan.request.incident_type)]
            assert "gkh" in resolve_services(row.get("service_rules") or [], [])


def test_third_card_is_sent_to_the_wrong_service_from_difficulty_2(type_rows: list[dict]) -> None:
    rows = _rows_by_code(type_rows)
    plan = plan_next(_spec(type_rows, difficulty=2, seq=3, own_service="gkh"))
    assert plan.request.wrong_service and plan.request.service == "gkh"
    row = rows[str(plan.request.incident_type)]
    assert all(rule.get("service") != "gkh" for rule in row.get("service_rules") or [])
    assert any("не та служба" in r for r in plan.reasons)
    assert "wrong_service" not in plan.prefer
    # Not at difficulty 1, not twice, but again after it was missed.
    assert not plan_next(
        _spec(type_rows, difficulty=1, seq=3, own_service="gkh")
    ).request.wrong_service
    found = EarlierCard("14.2.3.0", 90, True, [{"field": "service", "verdict": "found"}])
    again = plan_next(_spec(type_rows, seq=3, own_service="gkh", earlier=[found]))
    assert not again.request.wrong_service
    missed = EarlierCard("1.1.1.1", 40, False, [{"field": "service", "verdict": "missed"}])
    assert plan_next(
        _spec(type_rows, seq=2, own_service="gkh", earlier=[missed])
    ).request.wrong_service


# --- the lesson's service and variety (stand 17.10.2026) ------------------------------------


def test_lesson_service_is_the_teachers_choice_shared_round_the_group() -> None:
    members = ["a", "b", "c"]
    assert ai_plan.lesson_service([], members, "b", "gkh") == "gkh"  # no choice: own service
    assert ai_plan.lesson_service(["moek"], members, "b", "gkh") == "moek"
    two = ["gkh", "mosvodokanal"]
    assert [ai_plan.lesson_service(two, members, m, None) for m in members] == [
        "gkh",
        "mosvodokanal",
        "gkh",
    ]
    assert ai_plan.lesson_service(two, members, "stranger", None) in two


def test_service_groups_count_the_types_a_service_is_always_notified_of(
    type_rows: list[dict],
) -> None:
    groups = ai_plan.service_groups(type_rows)
    assert groups["gkh"]["1"] > 0  # ЖКХ takes part in fires
    # Only the services with an emergency squad: not 103, not the district councils.
    assert set(groups) <= set(ai_plan.DDS_SERVICES)
    assert "103" not in groups and "territorial_oiv" not in groups
    for code, count in groups["gkh"].items():
        rows = [
            r
            for r in type_rows
            if r.get("final_title")
            and str(r.get("group_code")) == code
            and "gkh" in resolve_services(r.get("service_rules") or [], [])
        ]
        assert len(rows) == count


def test_first_cards_spread_over_the_types_not_the_groups(type_rows: list[dict]) -> None:
    """Two groups of 3 and 6 types: each type about as likely, not each group."""
    firsts = [
        str(
            plan_next(
                _spec(type_rows, difficulty=1, own_service="gkh", trainee_key=f"l{i}|a")
            ).request.incident_type
        )
        for i in range(300)
    ]
    by_group: dict[str, int] = {}
    for code in firsts:
        by_group[_group(code)] = by_group.get(_group(code), 0) + 1
    sizes = {
        g: len(
            {
                str(r["code"])
                for r in type_rows
                if str(r.get("group_code")) == g
                and r.get("final_title")
                and "gkh" in resolve_services(r.get("service_rules") or [], [])
            }
        )
        for g in by_group
    }
    if len(sizes) > 1:
        big, small = max(sizes, key=sizes.get), min(sizes, key=sizes.get)
        if sizes[big] > sizes[small]:
            assert by_group[big] > by_group[small]


def test_a_type_the_trainee_had_in_another_lesson_is_not_given_again(
    type_rows: list[dict],
) -> None:
    first = plan_next(_spec(type_rows, difficulty=1, own_service="gkh"))
    again = plan_next(
        _spec(
            type_rows,
            difficulty=1,
            own_service="gkh",
            seen_types=[str(first.request.incident_type)],
        )
    )
    assert again.request.incident_type != first.request.incident_type
