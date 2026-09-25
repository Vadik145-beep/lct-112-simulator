"""Free generation: the call's prompt is read into the model while the caller's opening plays,
so the first reply does not wait 9-13 s for it (stand, 25.09.2026)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.dialog import service as dialog
from app.domain.evaluation.schemas import CallIntakeScenario, DialogTurn
from app.providers.dialog import ButtonsDialog, DialogContext, GenerateDialog
from app.providers.llm import ModelUnavailableError
from tests.providers.fake_model import FakeModel

SCENARIOS_DIR = Path(__file__).resolve().parents[3] / "data" / "seed" / "scenarios"


@pytest.fixture
def scenario() -> CallIntakeScenario:
    body = json.loads((SCENARIOS_DIR / "call_31-3_svist_gazovoy_truby.json").read_text("utf-8"))
    return CallIntakeScenario.model_validate(body)


def opened(scenario: CallIntakeScenario) -> DialogContext:
    """The call right after the pick-up: the caller's opening is turn 0."""
    opening = DialogTurn(role="caller", text=scenario.caller.opening, method="opening")
    return DialogContext(scenario=scenario, history=[opening], conversation_id="attempt-1")


async def test_warm_up_sends_what_the_first_reply_will_start_with(
    scenario: CallIntakeScenario,
) -> None:
    model = FakeModel(["", {"reply": "Свистит труба на кухне.", "topics": ["what_happened"]}])
    provider = GenerateDialog(model)
    await provider.warm(opened(scenario))
    await provider.reply(opened(scenario), "Что у вас случилось?")

    warm, first = model.calls
    assert warm["max_tokens"] == 1
    # Same slot and the same messages up to the operator's phrase: the cache is reused.
    assert warm["slot_key"] == first["slot_key"] == "attempt-1"
    assert warm["messages"][:-1] == first["messages"][:-1]
    assert len(first["messages"]) > 1


async def test_warm_up_without_a_model_is_quiet(scenario: CallIntakeScenario) -> None:
    provider = GenerateDialog(FakeModel([ModelUnavailableError("down")]))
    await provider.warm(opened(scenario))  # no exception: the call goes on as before


def lesson(mode: str) -> SimpleNamespace:
    return SimpleNamespace(dialog_mode=mode)


def attempt_after_pickup(scenario: CallIntakeScenario) -> SimpleNamespace:
    return SimpleNamespace(
        id="attempt-1",
        dialog=[{"role": "caller", "text": scenario.caller.opening, "method": "opening"}],
    )


@pytest.mark.parametrize("mode", ["select", "hybrid", "buttons", "cloud", None])
async def test_only_free_generation_is_warmed_up(
    mode: str | None, scenario: CallIntakeScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeModel([""])
    monkeypatch.setattr(dialog, "provider_for", lambda ts: GenerateDialog(model))
    dialog.warm_generation(lesson(mode), attempt_after_pickup(scenario), scenario)
    await asyncio.sleep(0)
    assert model.calls == []


async def test_free_generation_is_warmed_up_in_the_background(
    scenario: CallIntakeScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeModel([""])
    monkeypatch.setattr(dialog, "provider_for", lambda ts: GenerateDialog(model))
    dialog.warm_generation(lesson("generate"), attempt_after_pickup(scenario), scenario)
    assert model.calls == []  # started, not awaited: the pick-up does not wait for it
    await asyncio.gather(*dialog._warm_ups)
    assert [c["max_tokens"] for c in model.calls] == [1]
    assert model.calls[0]["slot_key"] == "attempt-1"


async def test_generation_degraded_without_a_model_is_not_warmed_up(
    scenario: CallIntakeScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a model every mode answers by keywords: there is nothing to warm up."""
    monkeypatch.setattr(dialog, "provider_for", lambda ts: ButtonsDialog())
    dialog.warm_generation(lesson("generate"), attempt_after_pickup(scenario), scenario)
    assert not dialog._warm_ups


async def test_the_duty_officer_is_warmed_up_the_same_way() -> None:
    """A card-response lesson: the officer's greeting is turn 0, then the dispatcher speaks."""
    from app.domain.evaluation.schemas import CardResponseScenario, ServiceCallRef
    from app.domain.scenarios import officers
    from app.providers.dialog import ROLE_OFFICER

    card = CardResponseScenario.model_validate(
        json.loads((SCENARIOS_DIR / "card_31-3_svist_gazovoy_truby.json").read_text("utf-8"))
    )
    officer = officers.officer_scenario(card, ServiceCallRef(service="mosgaz"), "Мосгаз")

    def ctx() -> DialogContext:
        greeting = DialogTurn(role="caller", text=officer.caller.opening, method="opening")
        return DialogContext(
            scenario=officer,
            history=[greeting],
            conversation_id="attempt-1:call-1",
            role=ROLE_OFFICER,
        )

    model = FakeModel(["", {"reply": "Адрес принял.", "topics": ["address"]}])
    provider = GenerateDialog(model)
    await provider.warm(ctx())
    await provider.reply(ctx(), "Запах газа, улица Вавилова, 81")

    warm, first = model.calls
    assert warm["slot_key"] == first["slot_key"] == "attempt-1:call-1"
    assert warm["messages"][:-1] == first["messages"][:-1]
    assert "ДЕЖУРНОГО" in warm["messages"][0]["content"]
