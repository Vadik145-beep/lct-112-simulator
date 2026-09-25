"""The squad leader says the report again with the report's own recording, not a synthesis
(stand, 25.09.2026: «готовые реплики» played Silero for «Повторяю: …»)."""

from __future__ import annotations

from app.domain.evaluation.schemas import BrigadeReport
from app.domain.scenarios.officers import report_replies

REPORT = BrigadeReport(
    status="arrived",
    text="Алло, это снова старший группы. Прибыли на место.",
    audio="tts/seed/card_1/report-arrived.mp3",
)


def test_the_report_said_again_plays_its_recording() -> None:
    replies = report_replies(REPORT, "4864")
    again = [r for r in replies if REPORT.text in r.text]
    assert {r.text for r in again} == {REPORT.text, f"Повторяю: {REPORT.text}"}
    assert all(r.audio == REPORT.audio for r in again)


def test_other_replies_keep_their_own_voice() -> None:
    others = [r for r in report_replies(REPORT, "4864") if REPORT.text not in r.text]
    assert others and all(r.audio is None for r in others)


def test_a_report_without_a_recording_changes_nothing() -> None:
    plain = REPORT.model_copy(update={"audio": None})
    assert all(r.audio is None for r in report_replies(plain, "4864"))
