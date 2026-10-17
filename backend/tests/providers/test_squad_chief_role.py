"""The customer's roles on the phone (#263): the officer of the own service's tab is the chief
of the emergency squad, the squad's reports come from him under that title (older cards are
renamed on the fly and lose a recording of the old words), other services keep their duty
officers."""

from __future__ import annotations

from pathlib import Path

from app.dialog import officer
from app.domain.scenarios import officers
from app.models import Attempt
from app.seed import _attach_report_audio
from tests.domain.evaluation.helpers import card_scenario

NAME = "card_gkh_1_tech_v_podezde"  # own service gkh


def opening(service: str, *, repeat: bool = False) -> str:
    built = officer.officer_scenario(
        card_scenario(NAME), service, "Служба", Attempt(), repeat=repeat
    )
    return built.caller.opening


def test_own_tab_is_the_squad_chief_other_tabs_their_duty_officers() -> None:
    assert opening("gkh") == "Начальник аварийной бригады ЕДЦ ЖКХ, слушаю."
    assert opening("gkh", repeat=True).startswith(
        "Начальник аварийной бригады ЕДЦ ЖКХ, слушаю. Карточку от вас уже принял."
    )
    assert opening("moek") == "Дежурный МОЭК, слушаю."


def test_old_wording_is_renamed() -> None:
    assert officers.renamed("Алло, диспетчер? Это старший группы реагирования. Выехали.") == (
        "Алло, диспетчер? Это начальник аварийной бригады. Выехали."
    )
    assert officers.renamed("Алло, диспетчер? Старший группы. Работы завершены.") == (
        "Алло, диспетчер? Начальник аварийной бригады. Работы завершены."
    )


def test_stored_report_of_old_wording_is_said_by_the_new_title_without_its_recording() -> None:
    scenario = card_scenario(NAME)
    old = scenario.reference.reports[0].model_copy(
        update={
            "text": "Алло, диспетчер? Это старший группы реагирования. Выехали.",
            "audio": "seed/audio/old.mp3",
        }
    )
    reference = scenario.reference.model_copy(update={"reports": [old]})
    stored = scenario.model_copy(update={"reference": reference})
    call = {"kind": "report", "report_status": old.status, "service": "gkh"}
    built = officer.call_scenario(stored, call, Attempt(service_calls=[]))
    assert built.caller.opening == "Алло, диспетчер? Это начальник аварийной бригады. Выехали."
    report = officer._report_for(stored, call, Attempt(service_calls=[]))
    assert report.audio is None


def test_seed_links_a_report_recording_only_when_it_says_the_same_words(tmp_path: Path) -> None:
    body = {
        "reference": {
            "reports": [
                {"status": "arrived", "text": "Алло, это снова начальник аварийной бригады."},
                {"status": "works_done", "text": "Работы завершены."},
            ]
        }
    }
    recorded = {
        "report-arrived": "Алло, это снова старший группы.",
        "report-works_done": "Работы завершены.",
    }
    linked = _attach_report_audio(body, lambda stem: f"audio/{stem}.mp3", recorded)
    reports = body["reference"]["reports"]
    assert linked == 1
    assert "audio" not in reports[0]
    assert reports[1]["audio"] == "audio/report-works_done.mp3"
