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


def greetings(service: str) -> list[tuple[int, str]]:
    built = officer.officer_scenario(card_scenario(NAME), service, "Служба", Attempt())
    return [(r.id, r.text) for r in built.replies if r.topic == "greeting"]


def test_greeted_mid_call_each_answers_by_his_own_title() -> None:
    """Greeted again, the chief does not turn into «Дежурный …» (19.10.2026); his line has its
    own id, so a voice cached for the duty officer's words is never played for it."""
    chief_id = officers.BUILTIN_REPLY_ID_BASE + officers.CHIEF_GREETING_REPLY_ID
    assert greetings("gkh") == [(chief_id, "Начальник аварийной бригады ЕДЦ ЖКХ, слушаю.")]
    assert greetings("moek") == [(officers.BUILTIN_REPLY_ID_BASE, "Дежурный МОЭК, слушаю.")]


def test_old_wording_is_renamed() -> None:
    assert officers.renamed("Алло, диспетчер? Это старший группы реагирования. Выехали.") == (
        "Алло, диспетчер? Это начальник аварийной бригады. Выехали."
    )
    assert officers.renamed("Алло, диспетчер? Старший группы. Работы завершены.") == (
        "Алло, диспетчер? Начальник аварийной бригады. Работы завершены."
    )


def test_works_done_is_said_once() -> None:
    """Stand 17.10.2026: the reference comment itself started with «Работы завершены»."""
    said = "Алло, диспетчер? Начальник аварийной бригады. Работы завершены: работы завершены, "
    assert officers.renamed(said + "пострадавших нет.") == (
        "Алло, диспетчер? Начальник аварийной бригады. Работы завершены, пострадавших нет."
    )
    assert officers.renamed("Работы завершены: работы выполнены.") == "Работы завершены."
    # No colons in a report said aloud (18.10.2026).
    assert officers.renamed("Работы завершены: заменили участок трубы.") == (
        "Работы завершены, заменили участок трубы."
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
    assert built.caller.opening.startswith(
        "Алло, диспетчер? Это начальник аварийной бригады. Выехали. Наряд "
    )
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


def test_the_departure_report_names_the_squad() -> None:
    """Stand 18.10.2026: on the head's vehicle nobody named the squad."""
    said = "Алло, диспетчер? Начальник аварийной бригады. Выехали: улица Свободы, дом 42."
    # One digit at a time, with pauses: the cloud's voice read «8513» as «8-5/13» and «4641» as
    # «4-641» (18.10.2026).
    assert officers.with_order(said, "response_started", "2750") == (
        f"{said} Наряд два, семь, пять, ноль."
    )
    older = f"{said} Наряд два-семь-пять-ноль."
    assert officers.with_order(older, "response_started", "2750") == older
    named = f"{said} Наряд 2750."
    assert officers.with_order(named, "response_started", "2750") == named
    assert officers.with_order("Прибыли.", "arrived", "2750") == "Прибыли."


def test_a_squad_number_said_digit_by_digit_is_still_the_number() -> None:
    from app.dialog.officer import says_number

    assert says_number("Принял, выезжаем. Наряд восемь-пять-один-три.", "8513")
    assert says_number("наряд 8-5-1-3", "8513")
    assert says_number("Принял, выезжаем. Наряд: восемь, пять, один, три.", "8513")
    assert says_number("Наряд восемь, ноль, ноль, ноль.", "8000")
    assert says_number("Наряд четыре, ноль, шесть, один.", "4061")
    assert not says_number("Наряд четыре, шесть, один.", "4061")
    assert "«восемь, пять, один, три»" in officers.said_order("8513")


def test_reports_are_said_without_colons_and_repeats() -> None:
    """Decision of 18.10.2026: «Работы завершены, газоснабжение восстановлено»."""
    head = "Алло, диспетчер? Начальник аварийной бригады. "
    assert officers.renamed(head + "Работы завершены: газоснабжение восстановлено.") == (
        head + "Работы завершены, газоснабжение восстановлено."
    )
    assert officers.renamed(head + "Работы завершены: работы завершены, пострадавших нет.") == (
        head + "Работы завершены, пострадавших нет."
    )
    assert officers.renamed(
        head + "Работы завершены: работы по устранению аварии завершены, вода подана."
    ) == (head + "Работы по устранению аварии завершены, вода подана.")
    assert officers.renamed(head + "Докладываю: перекрыт стояк.") == (
        head + "Работы начаты, перекрыт стояк."
    )
    assert officers.renamed(head + "Докладываю: проводятся работы на месте.") == (
        head + "Проводятся работы на месте."
    )
    assert officers.renamed("Выехали по адресу: улица Свободы, дом 42.") == (
        "Выехали по адресу улица Свободы, дом 42."
    )
    assert "Мосгаз утечку" in officers.renamed("Работы завершены: мосгаз утечку не нашёл.")


def test_every_seed_report_is_said_without_a_colon() -> None:
    import json

    from tests.domain.evaluation.helpers import SCENARIOS_DIR

    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        body = json.loads(path.read_text("utf-8"))
        if body.get("kind") != "card_response":
            continue
        reports = (body.get("reference") or {}).get("reports")
        for report in reports if reports is not None else officers.default_reports(body):
            said = officers.renamed(report["text"])
            assert ":" not in said, (path.name, said)
            assert said.lower().count("работы завершены") <= 1, (path.name, said)
