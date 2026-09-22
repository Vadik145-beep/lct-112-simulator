"""Reports of the squad to the dispatcher (customer, 21.09.2026): the default timeline of a
card, the squad leader's dialog, the officer's answer about the progress, and the three
detectors — a status set before the report, a report never reflected, a report nobody
answered (issue #103)."""

from __future__ import annotations

from app.domain.evaluation.card_response import evaluate_card_response
from app.domain.evaluation.detectors import REPORT_REACTION_SECONDS
from app.domain.evaluation.schemas import BrigadeReport, CardResponseAttempt, DialogTurn
from app.domain.scenarios import officers
from app.domain.scenarios.validate import fill_from_reference
from app.providers.dialog import ROLE_OFFICER, ButtonsDialog, DialogContext
from tests.domain.evaluation.helpers import (
    STEP_SECONDS,
    at,
    card_scenario,
    grammar_ok,
    perfect_card_attempt,
    perfect_report_calls,
)

NAME = "card_gkh_1_tech_v_podezde"  # an accepted card with a call and the default reports


def errors_of(scenario, attempt) -> dict[str, str]:
    result = evaluate_card_response(scenario, attempt, grammar=grammar_ok())
    return {e.code: e.explanation for e in result.errors}


def with_changes(attempt: CardResponseAttempt, **changes) -> CardResponseAttempt:
    return CardResponseAttempt.model_validate({**attempt.model_dump(), **changes})


# --- the timeline ------------------------------------------------------------------------------


def test_default_reports_follow_the_reference_chain() -> None:
    scenario = card_scenario(NAME)
    reports = officers.default_reports(scenario.model_dump())
    assert [r["status"] for r in reports] == [
        "response_started",
        "arrived",
        "works_started",
        "works_done",
    ]
    assert all(r["after_seconds"] > 0 for r in reports)
    assert "улица Свободы, дом 42, корпус 2" in reports[0]["text"]
    assert reports[3]["text"].startswith("Старший наряда. Работы завершены: заменён")


def test_rejected_card_has_no_reports() -> None:
    scenario = card_scenario("card_2-1_dubl")
    assert officers.default_reports(scenario.model_dump()) == []


def test_fill_from_reference_adds_reports_once_and_keeps_an_explicit_empty_list() -> None:
    body = card_scenario(NAME).model_dump()
    body["reference"].pop("reports")
    filled = fill_from_reference(body, _refs())
    assert [r["status"] for r in filled["reference"]["reports"]] == [
        "response_started",
        "arrived",
        "works_started",
        "works_done",
    ]
    body["reference"]["reports"] = []
    assert fill_from_reference(body, _refs())["reference"]["reports"] == []


def _refs():
    from app.domain.scenarios.validate import ReferenceCodes

    return ReferenceCodes(incident_types={}, services=set(), flags=set())


def test_seed_cards_with_calls_have_reports_for_every_progress_step() -> None:
    from tests.domain.evaluation.helpers import SCENARIOS_DIR, load

    for path in sorted(SCENARIOS_DIR.glob("card_*.json")):
        body = load(path.stem)
        reference = body["reference"]
        if reference["decision"] != "accept" or "service_calls" not in reference:
            continue
        chain = [s["status"] for s in reference["status_chain"] if s["status"] != "accepted"]
        assert [r["status"] for r in reference["reports"]] == chain, path.name


# --- the squad's dialog ------------------------------------------------------------------------


def test_squad_state_is_the_last_report_delivered() -> None:
    assert officers.squad_state([]) == officers.SQUAD_STATE_PENDING
    assert officers.squad_state(["response_started"]) == "response_started"
    assert officers.squad_state(["response_started", "arrived"]) == "arrived"


async def test_officer_answers_about_the_progress_from_the_squad_state() -> None:
    scenario = card_scenario(NAME)
    ref = officers.default_service_calls(scenario.model_dump())[0]
    ref = type(scenario.reference.service_calls[0]).model_validate(ref)
    for state, phrase in (
        (officers.SQUAD_STATE_PENDING, "Бригаду собираем"),
        ("response_started", "в пути"),
        ("arrived", "на месте"),
        ("works_done", "завершены"),
    ):
        officer = officers.officer_scenario(scenario, ref, "ОДС ЖКХ", state)
        ctx = DialogContext(scenario=officer, history=[], conversation_id="t", role=ROLE_OFFICER)
        reply = await ButtonsDialog().reply(ctx, "Где сейчас бригада, выехали?")
        assert reply.topics == ["progress"], state
        assert phrase in reply.text, (state, reply.text)


async def test_squad_leader_repeats_the_report_and_confirms() -> None:
    scenario = card_scenario(NAME)
    report = scenario.reference.reports[1]
    leader = officers.report_scenario(scenario, report, "ОДС ЖКХ")
    assert leader.caller.opening == report.text
    # In a real call the report is turn 0 of the history (dialog.officer.answer).
    opening = DialogTurn(role="caller", text=report.text, topics=["report"])
    ctx = DialogContext(scenario=leader, history=[opening], conversation_id="t", role=ROLE_OFFICER)
    again = await ButtonsDialog().reply(ctx, "Повторите, плохо слышно")
    assert report.text in again.text
    ok = await ButtonsDialog().reply(ctx, "Принято, ставлю прибытие")
    assert ok.topics[0] in {"confirm", "progress", "unknown"}


# --- the detectors -----------------------------------------------------------------------------


def test_perfect_attempt_with_reports_triggers_nothing() -> None:
    scenario = card_scenario(NAME)
    assert errors_of(scenario, perfect_card_attempt(scenario)) == {}


def test_statuses_set_before_the_reports_are_an_error() -> None:
    scenario = card_scenario(NAME)
    perfect = perfect_card_attempt(scenario)
    # The squad reported nothing yet, the dispatcher clicked the whole chain through.
    attempt = with_changes(
        perfect,
        service_calls=[c.model_dump() for c in perfect.service_calls if c.kind == "outgoing"],
    )
    errors = errors_of(scenario, attempt)
    assert "status_before_report" in errors
    assert "«Прибытие»" in errors["status_before_report"]
    assert "report_not_reflected" not in errors


def test_a_status_set_before_its_own_report_is_named() -> None:
    scenario = card_scenario(NAME)
    perfect = perfect_card_attempt(scenario)
    log = [e.model_dump() for e in perfect.status_log]
    # «Прибытие» jumps ahead of its report (STEP_SECONDS["arrived"] - 30).
    for entry in log:
        if entry["status"] == "arrived":
            entry["at"] = at(STEP_SECONDS["arrived"] - 60)
    attempt = with_changes(perfect, status_log=log)
    errors = errors_of(scenario, attempt)
    assert "status_before_report" in errors
    assert "«Прибытие»" in errors["status_before_report"]
    assert "«Начало реагирования»" not in errors["status_before_report"]


def test_report_without_the_status_is_an_error() -> None:
    scenario = card_scenario(NAME)
    perfect = perfect_card_attempt(scenario)
    log = [e.model_dump() for e in perfect.status_log if e.status != "arrived"]
    attempt = with_changes(perfect, status_log=log)
    errors = errors_of(scenario, attempt)
    assert "report_not_reflected" in errors
    assert "«Прибытие»" in errors["report_not_reflected"]


def test_status_long_after_the_report_is_late() -> None:
    scenario = card_scenario(NAME)
    perfect = perfect_card_attempt(scenario)
    log = [e.model_dump() for e in perfect.status_log]
    for entry in log:
        if entry["status"] == "arrived":
            entry["at"] = at(STEP_SECONDS["arrived"] + REPORT_REACTION_SECONDS + 5)
    attempt = with_changes(perfect, status_log=log)
    errors = errors_of(scenario, attempt)
    assert "report_not_reflected" in errors
    assert "позже норматива" in errors["report_not_reflected"]


def test_report_nobody_answered_is_an_error_and_is_not_counted_as_delivered() -> None:
    scenario = card_scenario(NAME)
    perfect = perfect_card_attempt(scenario)
    calls = []
    for call in perfect.service_calls:
        row = call.model_dump()
        if call.kind == "report" and call.report_status == "arrived":
            # The squad called, the dispatcher never picked up: no dialog, «не принят».
            row.update(answered=False, dialog=[], end_reason="not_taken")
        calls.append(row)
    errors = errors_of(scenario, with_changes(perfect, service_calls=calls))
    assert "report_not_taken" in errors
    assert "«Прибытие»" in errors["report_not_taken"]
    # The status is in the card, and the dispatcher is not blamed for reflecting it late:
    # the report they never heard is not a delivered one. Setting it anyway is the other
    # error — the status went into the card without the information behind it.
    assert "report_not_reflected" not in errors
    assert "«Прибытие»" in errors["status_before_report"]


def test_report_still_ringing_when_the_card_closed_is_not_blamed() -> None:
    scenario = card_scenario(NAME)
    perfect = perfect_card_attempt(scenario)
    calls = [c.model_dump() for c in perfect.service_calls]
    calls.append(
        {
            "service": scenario.service,
            "kind": "report",
            "report_status": "works_done",
            "started_at": at(STEP_SECONDS["works_done"]),
            "answered": False,
            "ended_at": at(STEP_SECONDS["works_done"] + 2),
            "end_reason": "card_closed",
            "dialog": [],
        }
    )
    assert "report_not_taken" not in errors_of(scenario, with_changes(perfect, service_calls=calls))


def test_card_without_reports_is_judged_as_before() -> None:
    scenario = card_scenario("card_2-1_zadymlenie_musoroprovoda")
    assert scenario.reference.reports == []
    errors = errors_of(scenario, perfect_card_attempt(scenario))
    assert "status_before_report" not in errors
    assert "report_not_reflected" not in errors


def test_reports_do_not_count_as_the_dispatcher_calls() -> None:
    scenario = card_scenario(NAME)
    attempt = perfect_card_attempt(scenario)
    only_reports = with_changes(attempt, service_calls=perfect_report_calls(scenario))
    result = evaluate_card_response(scenario, only_reports, grammar=grammar_ok())
    assert result.components["service_call"].score == 0
    assert "service_not_informed" in {e.code for e in result.errors}


def test_report_model_defaults() -> None:
    report = BrigadeReport(status="arrived", text="На месте.")
    assert report.after_seconds > 0
    assert CardResponseAttempt(issued_at=at(0)).service_calls == []
