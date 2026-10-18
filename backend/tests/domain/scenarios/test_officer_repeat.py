"""Repeat call to a service officer (замечание пользователя 27.09.2026): the card was passed
on an earlier call, the officer does not take it again."""

from __future__ import annotations

from app.domain.evaluation.schemas import ServiceCallRef
from app.domain.scenarios import officers
from app.telephony import vapi
from tests.domain.evaluation.helpers import card_scenario


def test_repeat_officer_opens_with_the_squad_and_asks_nothing() -> None:
    # The squad of the dispatcher's own service: another service tells nothing of its own.
    scenario = card_scenario().model_copy(update={"service": "mosgaz"})
    ref = ServiceCallRef(service="mosgaz")
    first = officers.officer_scenario(scenario, ref, "Мосгаз", "works_started")
    again = officers.officer_scenario(scenario, ref, "Мосгаз", "works_started", repeat=True)

    assert first.caller.opening == officers.greeting("mosgaz", "Мосгаз")
    assert again.caller.opening.startswith(first.caller.opening)
    assert "уже приняли" in again.caller.opening
    assert officers.PROGRESS_REPLIES["works_started"] in again.caller.opening
    assert again.required_topics == []
    assert first.required_topics == list(ref.required_facts)
    assert not [r for r in again.replies if r.text.rstrip().endswith("?")]
    assert [r for r in first.replies if r.text.rstrip().endswith("?")]
    assert officers.REPEAT_FACT_KEY in again.caller.facts
    assert officers.REPEAT_FACT_KEY not in first.caller.facts


def test_cloud_gets_the_repeat_instruction_only_on_a_repeat_call() -> None:
    scenario = card_scenario()
    ref = ServiceCallRef(service="mosgaz")
    first = officers.officer_scenario(scenario, ref, "Мосгаз", "arrived")
    again = officers.officer_scenario(scenario, ref, "Мосгаз", "arrived", repeat=True)
    assert "принять информацию" in vapi.role_prompt(first)
    prompt = vapi.role_prompt(again)
    assert "уже принял от него" in prompt
    assert "принять информацию" not in prompt


def test_repeat_officer_gets_its_own_generate_prompt() -> None:
    from app.domain.evaluation.schemas import ServiceCallRef
    from app.domain.scenarios import officers
    from app.providers import dialog as dlg
    from tests.domain.evaluation.helpers import card_scenario

    scenario = card_scenario().model_copy(update={"service": "mosgaz"})
    ref = ServiceCallRef(service="mosgaz")
    first = officers.officer_scenario(scenario, ref, "Мосгаз", "arrived")
    again = officers.officer_scenario(scenario, ref, "Мосгаз", "arrived", repeat=True)

    def system(officer):
        ctx = dlg.DialogContext(
            scenario=officer, history=[], conversation_id="t", role=dlg.ROLE_OFFICER
        )
        return dlg.generate_messages(ctx, "Как там у вас?")[0]["content"]

    assert "спроси один следующий факт" in system(first)
    assert "СНОВА" in system(again)
    assert "спроси один следующий факт" not in system(again)


def test_repeat_officer_guard_replaces_questions_and_repeats() -> None:
    from app.domain.evaluation.schemas import DialogTurn, ServiceCallRef
    from app.domain.scenarios import officers
    from app.providers import dialog as dlg
    from tests.domain.evaluation.helpers import card_scenario

    own = card_scenario().model_copy(update={"service": "mosgaz"})
    officer = officers.officer_scenario(
        own, ServiceCallRef(service="mosgaz"), "Мосгаз", "pending", repeat=True
    )
    progress = officers.PROGRESS_REPLIES["pending"]
    ctx = dlg.DialogContext(
        scenario=officer, history=[], conversation_id="t", role=dlg.ROLE_OFFICER
    )
    assert "бригада в пути" not in dlg.generate_messages(ctx, "Как там?")[0]["content"].lower()
    assert progress in dlg.generate_messages(ctx, "Как там?")[0]["content"]

    asked = dlg.repeat_officer_guard(ctx, "Как у вас? Адрес какой?", "Как дела?", [])
    assert asked is not None and asked.text == progress

    said = dlg.REPEAT_CONFIRMATIONS[0]
    ctx.history = [DialogTurn(role="caller", text=said, topics=["confirm"])]
    again = dlg.repeat_officer_guard(ctx, said, "Там ещё запах появился", [])
    assert again is not None and again.text == dlg.REPEAT_CONFIRMATIONS[1]

    assert dlg.repeat_officer_guard(ctx, "Работаем по карточке.", "Ну как?", []) is None


def test_another_service_names_no_squad_and_tells_no_progress() -> None:
    """Stand 18.10.2026: the district council «sent squad 2-348». The dispatcher only tells
    another service; its squad, number and progress are none of his card."""
    scenario = card_scenario().model_copy(update={"service": "gkh"})
    ref = ServiceCallRef(service="territorial_oiv")
    first = officers.officer_scenario(scenario, ref, "Управа", "arrived")
    again = officers.officer_scenario(scenario, ref, "Управа", "arrived", repeat=True)
    assert "твой наряд" not in first.caller.facts
    assert "где бригада" not in first.caller.facts
    assert not [r for r in first.replies if "наряд" in r.text.lower()]
    assert officers.OTHER_SERVICE_CONFIRM in [r.text for r in first.replies]
    assert officers.PROGRESS_REPLIES["arrived"] not in again.caller.opening
    assert "отрабатываем" in again.caller.opening


def test_another_service_gets_its_own_local_prompt() -> None:
    from app.providers import dialog as dlg

    scenario = card_scenario().model_copy(update={"service": "gkh"})
    officer = officers.officer_scenario(scenario, ServiceCallRef(service="mosgaz"), "Мосгаз")
    ctx = dlg.DialogContext(
        scenario=officer, history=[], conversation_id="t", role=dlg.ROLE_OFFICER
    )
    system = dlg.generate_messages(ctx, "Пострадавших нет")[0]["content"]
    assert "ДРУГОЙ службы" in system
    assert "Номер своего наряда НЕ называешь" in system
