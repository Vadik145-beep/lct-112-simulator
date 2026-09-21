"""Scenario API: library and filters, editing with approved-part locks, replies, approval,
generation and revision jobs, preview dialog, own recordings, methodical documents, access."""

from __future__ import annotations

import asyncio
import io
import json
import wave

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog, Scenario
from app.scenarios import jobs
from tests.api.conftest import DATA_DIR
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)

SEED_CALL = json.loads(
    (DATA_DIR / "seed" / "scenarios" / "call_2-1_zadymlenie_musoroprovoda.json").read_text(
        encoding="utf-8"
    )
)


async def teacher(client: AsyncClient) -> dict[str, str]:
    return bearer(await login(client, "teacher1"))


def draft_body(**overrides) -> dict:
    """A fresh call-intake draft from the seed file: nothing approved yet."""
    body = json.loads(json.dumps(SEED_CALL))
    body["title"] = "Тестовый сценарий задымления"
    body["ticket_ref"] = None
    body.pop("status", None)
    for reply in body["replies"]:
        reply["approved"] = False
        reply["audio"] = None
    body.update(overrides)
    return body


async def create_draft(client: AsyncClient, headers: dict, **overrides) -> dict:
    r = await client.post(
        "/api/scenarios", headers=headers, json={"body": draft_body(**overrides), "status": "draft"}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def wait_job(client: AsyncClient, headers: dict, job_id: str) -> dict:
    for _ in range(100):
        r = await client.get(f"/api/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] in {"done", "failed"}:
            return job
        await asyncio.sleep(0.1)
    raise AssertionError(f"задача не завершилась: {job}")


# ---------------------------------------------------------------- list, options, access


async def test_options_and_list_filters(client: AsyncClient) -> None:
    headers = await teacher(client)
    r = await client.get("/api/scenarios/options", headers=headers)
    assert r.status_code == 200, r.text
    options = r.json()
    assert {p["code"] for p in options["personas"]} >= {"calm", "child", "angry_customer"}
    assert options["generation_method"] == "template"  # no LLM_GEN_URL in tests

    r = await client.get("/api/scenarios", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert r.json()["total"] == len(items) >= 10  # seed scenarios
    seed = next(i for i in items if i["ticket_ref"] == "2-1" and i["kind"] == "call_intake")
    assert seed["status"] == "approved" and seed["incident_group_title"] == "Пожары и задымления"
    assert seed["replies_total"] == seed["replies_approved"] > 0

    r = await client.get("/api/scenarios", headers=headers, params={"kind": "card_response"})
    assert {i["kind"] for i in r.json()["items"]} == {"card_response"}
    r = await client.get("/api/scenarios", headers=headers, params={"group": "1"})
    assert r.json()["total"] >= 2
    assert all(i["incident_group_code"] == "1" for i in r.json()["items"])
    r = await client.get("/api/scenarios", headers=headers, params={"ticket": "2-2"})
    assert {i["ticket_ref"] for i in r.json()["items"]} == {"2-2"}
    r = await client.get("/api/scenarios", headers=headers, params={"q": "МУСОРОПРОВОД"})
    assert r.json()["total"] >= 1
    r = await client.get("/api/scenarios", headers=headers, params={"status": "bogus"})
    assert r.status_code == 422


async def test_student_has_no_access(client: AsyncClient) -> None:
    student = bearer(await login(client, "student1"))
    assert (await client.get("/api/scenarios", headers=student)).status_code == 403
    assert (
        await client.post(
            "/api/scenarios/generate",
            headers=student,
            json={"kind": "call_intake", "phrase": "пожар"},
        )
    ).status_code == 403


# ---------------------------------------------------------------- card, edit, locks


async def test_card_shows_reference_checks_and_services(client: AsyncClient) -> None:
    headers = await teacher(client)
    created = await create_draft(client, headers)
    assert created["status"] == "draft" and created["source"] == "manual"
    assert created["problems"] == []
    assert created["incident_type_title"] == "задымление: мусоропровод"
    assert [s["code"] for s in created["services"]][:1] == ["101"]
    assert created["versions"] == [{**created["versions"][0], "version": 1, "is_current": True}]
    assert all(not r["approved"] and r["voicing"] == "none" for r in created["replies"])
    # Signs follow the classifier row, whatever the editor typed.
    assert created["body"]["reference_card"]["signs_path"] == ["жилой дом", "мусоропровод", "дым"]

    r = await client.get(f"/api/scenarios/{created['id']}", headers=headers)
    assert r.status_code == 200 and r.json()["title"] == "Тестовый сценарий задымления"


async def test_invalid_body_and_unknown_codes(client: AsyncClient) -> None:
    headers = await teacher(client)
    r = await client.post(
        "/api/scenarios", headers=headers, json={"body": {"kind": "call_intake", "title": "x"}}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_scenario"

    body = draft_body()
    body["reference_card"]["incident_type"] = "9.9.9.9"
    r = await client.post("/api/scenarios", headers=headers, json={"body": body})
    assert r.status_code == 201  # a draft may carry problems…
    created = r.json()
    assert any("9.9.9.9" in p for p in created["problems"])
    r = await client.post(
        f"/api/scenarios/{created['id']}/approve", headers=headers, json={"confirm_grammar": True}
    )
    assert r.status_code == 422  # …but cannot be approved with them
    assert "9.9.9.9" in r.json()["error"]["message"]


async def test_put_edits_draft_but_not_approved_parts(client: AsyncClient) -> None:
    headers = await teacher(client)
    created = await create_draft(client, headers)
    scenario_id = created["id"]

    body = dict(created["body"])
    body["title"] = "Новое название"
    body["replies"][0]["text"] = "Изменённая реплика"
    r = await client.put(f"/api/scenarios/{scenario_id}", headers=headers, json={"body": body})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Новое название"
    assert r.json()["replies"][0]["text"] == "Изменённая реплика"

    body["kind"] = "card_response"
    r = await client.put(f"/api/scenarios/{scenario_id}", headers=headers, json={"body": body})
    assert r.status_code == 422 and r.json()["error"]["code"] == "kind_locked"

    r = await client.post(
        f"/api/scenarios/{scenario_id}/approve", headers=headers, json={"confirm_grammar": True}
    )
    assert r.status_code == 200, r.text
    approved = r.json()
    assert approved["status"] == "approved" and approved["fully_approved"]
    assert all(reply["approved"] for reply in approved["replies"])

    body = dict(approved["body"])
    body["replies"][0]["text"] = "Ещё раз"
    r = await client.put(f"/api/scenarios/{scenario_id}", headers=headers, json={"body": body})
    assert r.status_code == 409 and r.json()["error"]["code"] == "approved_locked"
    body = dict(approved["body"])
    body["reference_card"] = {**body["reference_card"], "description": "Другой эталон"}
    r = await client.put(f"/api/scenarios/{scenario_id}", headers=headers, json={"body": body})
    assert r.status_code == 409
    assert "Переделать" in r.json()["error"]["message"]

    # Adding a reply to an approved scenario sends it back to review until approved.
    r = await client.post(
        f"/api/scenarios/{scenario_id}/replies",
        headers=headers,
        json={"topic": "time", "text": "Минут десять назад."},
    )
    assert r.status_code == 201 and r.json()["status"] == "review"
    new_id = r.json()["replies"][-1]["id"]
    r = await client.post(
        f"/api/scenarios/{scenario_id}/replies/approve",
        headers=headers,
        json={"reply_ids": [new_id], "confirm_grammar": True},
    )
    assert r.status_code == 200 and r.json()["status"] == "approved"


async def test_reply_edit_approve_lock_delete(client: AsyncClient) -> None:
    headers = await teacher(client)
    created = await create_draft(client, headers)
    sid = created["id"]
    first, second = created["replies"][0]["id"], created["replies"][1]["id"]

    r = await client.put(
        f"/api/scenarios/{sid}/replies/{first}",
        headers=headers,
        json={"text": "Новый текст", "topic": "danger"},
    )
    assert r.status_code == 200, r.text
    edited = next(x for x in r.json()["replies"] if x["id"] == first)
    assert (edited["text"], edited["topic"], edited["topic_title"]) == (
        "Новый текст",
        "danger",
        "Угроза и обстановка",
    )
    r = await client.put(
        f"/api/scenarios/{sid}/replies/{first}", headers=headers, json={"topic": "nonsense"}
    )
    assert r.status_code == 422

    r = await client.post(
        f"/api/scenarios/{sid}/replies/approve",
        headers=headers,
        json={"reply_ids": [first], "confirm_grammar": True},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert next(x for x in out["replies"] if x["id"] == first)["approved"]
    assert not next(x for x in out["replies"] if x["id"] == second)["approved"]
    assert out["status"] == "review" and not out["fully_approved"]

    r = await client.put(
        f"/api/scenarios/{sid}/replies/{first}", headers=headers, json={"text": "x"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "approved_locked"
    r = await client.delete(f"/api/scenarios/{sid}/replies/{first}", headers=headers)
    assert r.status_code == 409
    r = await client.delete(f"/api/scenarios/{sid}/replies/{second}", headers=headers)
    assert r.status_code == 200 and all(x["id"] != second for x in r.json()["replies"])
    r = await client.delete(f"/api/scenarios/{sid}/replies/9999", headers=headers)
    assert r.status_code == 404


async def test_own_recording_upload(client: AsyncClient) -> None:
    headers = await teacher(client)
    created = await create_draft(client, headers)
    sid, rid = created["id"], created["replies"][0]["id"]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * 1600)
    r = await client.post(
        f"/api/scenarios/{sid}/replies/{rid}/audio",
        headers=headers,
        files={"file": ("own.wav", buffer.getvalue(), "audio/wav")},
    )
    assert r.status_code == 200, r.text
    reply = next(x for x in r.json()["replies"] if x["id"] == rid)
    assert reply["audio_url"] and reply["audio_url"].endswith(f"/v1/r{rid}.wav")
    assert reply["voicing"] == "done"
    media = await client.get(reply["audio_url"], headers=headers)
    assert media.status_code == 200 and media.content[:4] == b"RIFF"
    r = await client.post(
        f"/api/scenarios/{sid}/replies/{rid}/audio",
        headers=headers,
        files={"file": ("own.ogg", b"OggS", "audio/ogg")},
    )
    assert r.status_code == 415


# ---------------------------------------------------------------- jobs


async def test_generate_by_phrase_creates_review_scenario(client: AsyncClient) -> None:
    headers = await teacher(client)
    r = await client.post(
        "/api/scenarios/generate",
        headers=headers,
        json={
            "kind": "call_intake",
            "phrase": "пожар в подземном паркинге, звонит ребёнок",
            "persona": "child",
            "difficulty": 2,
        },
    )
    assert r.status_code == 202, r.text
    job = await wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["type"] == "generate" and job["progress"] == 100
    scenario_id = job["result"]["scenario_id"]

    r = await client.get(f"/api/scenarios/{scenario_id}", headers=headers)
    assert r.status_code == 200
    scenario = r.json()
    assert scenario["source"] == "generated" and scenario["status"] == "review"
    assert scenario["problems"] == []
    assert scenario["body"]["caller"]["persona"] == "child"
    assert scenario["difficulty"] == 2
    assert scenario["generation"]["method"] == "template"
    assert scenario["generation"]["phrase"] == "пожар в подземном паркинге, звонит ребёнок"
    assert len(scenario["replies"]) >= 12

    r = await client.post(
        "/api/scenarios/generate",
        headers=headers,
        json={"kind": "card_response", "phrase": "запах газа в подъезде", "both_kinds": True},
    )
    job = await wait_job(client, headers, r.json()["job_id"])
    assert [s["kind"] for s in job["result"]["scenarios"]] == ["call_intake", "card_response"]

    r = await client.post(
        "/api/scenarios/generate",
        headers=headers,
        json={"kind": "call_intake", "phrase": "пожар", "persona": "alien"},
    )
    assert r.status_code == 422
    other = bearer(await login(client, "teacher2"))
    assert (await client.get(f"/api/jobs/{job['id']}", headers=other)).status_code == 403
    assert (await client.get("/api/jobs/" + "0" * 32, headers=headers)).status_code == 404


async def test_revise_keeps_approved_replies_in_new_version(client: AsyncClient) -> None:
    headers = await teacher(client)
    created = await create_draft(client, headers, ticket_ref="2-1")
    sid = created["id"]
    first = created["replies"][0]["id"]
    r = await client.post(
        f"/api/scenarios/{sid}/replies/approve",
        headers=headers,
        json={"reply_ids": [first], "confirm_grammar": True},
    )
    assert r.status_code == 200
    approved_text = next(x for x in r.json()["replies"] if x["id"] == first)["text"]

    r = await client.post(
        f"/api/scenarios/{sid}/revise",
        headers=headers,
        json={"comment": "Сделай заявителя растерянным"},
    )
    assert r.status_code == 202, r.text
    job = await wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["version"] == 2

    r = await client.get(f"/api/scenarios/{sid}", headers=headers)
    scenario = r.json()
    assert scenario["current_version"] == 2
    assert [v["version"] for v in scenario["versions"]] == [2, 1]
    assert scenario["versions"][0]["revision_comment"] == "Сделай заявителя растерянным"
    kept = next(x for x in scenario["replies"] if x["id"] == first)
    assert kept["approved"] and kept["text"] == approved_text
    assert sum(1 for x in scenario["replies"] if not x["approved"]) >= 10
    assert scenario["generation"]["comment"] == "Сделай заявителя растерянным"

    r = await client.get(f"/api/scenarios/{sid}/versions/1", headers=headers)
    assert r.status_code == 200 and r.json()["body"]["title"] == "Тестовый сценарий задымления"
    assert (
        await client.get(f"/api/scenarios/{sid}/versions/9", headers=headers)
    ).status_code == 404


# ---------------------------------------------------------------- preview and grammar


async def test_preview_dialog_answers_from_replies(client: AsyncClient) -> None:
    headers = await teacher(client)
    created = await create_draft(client, headers)
    r = await client.post(
        f"/api/scenarios/{created['id']}/preview-dialog",
        headers=headers,
        json={"text": "Скажите адрес", "history": []},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert "address" in out["operator_topics"]
    assert out["reply_id"] is not None and out["reply"]
    assert out["method"] in {"buttons", "select"}  # no model in tests → keyword path
    r = await client.post(
        f"/api/scenarios/{created['id']}/preview-dialog",
        headers=headers,
        json={
            "text": "Есть пострадавшие?",
            "history": [
                {"role": "operator", "text": "Скажите адрес"},
                {"role": "caller", "text": out["reply"]},
            ],
        },
    )
    assert r.status_code == 200 and "injured" in r.json()["operator_topics"]


async def test_grammar_report_without_languagetool(client: AsyncClient) -> None:
    headers = await teacher(client)
    created = await create_draft(client, headers)
    r = await client.post(f"/api/scenarios/{created['id']}/grammar", headers=headers)
    assert r.status_code == 200
    assert r.json()["available"] is False and r.json()["issues"] == []
    # Without a grammar service approval needs no confirmation.
    r = await client.post(f"/api/scenarios/{created['id']}/approve", headers=headers, json={})
    assert r.status_code == 200 and r.json()["status"] == "approved"


# ---------------------------------------------------------------- methodical documents


async def test_reference_docs_roundtrip(client: AsyncClient) -> None:
    headers = await teacher(client)
    text = (
        "Порядок работы с карточкой происшествия.\n\n"
        "Диспетчер обязан проставить статус «Принята» в течение тридцати секунд.\n\n"
        "При невозможности реагирования проставляется «Не принята» с комментарием о причине."
    )
    r = await client.post(
        "/api/reference/docs",
        headers=headers,
        files={"file": ("методичка тест.txt", text.encode("utf-8"), "text/plain")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "методичка тест" and r.json()["chunks"] == 3
    names = [d["name"] for d in (await client.get("/api/reference/docs", headers=headers)).json()]
    assert "методичка тест" in names
    r = await client.post(
        "/api/reference/docs",
        headers=headers,
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
    )
    assert r.status_code == 415
    r = await client.delete("/api/reference/docs/методичка тест", headers=headers)
    assert r.status_code == 204
    assert (
        await client.delete("/api/reference/docs/методичка тест", headers=headers)
    ).status_code == 404


@pytest.fixture(autouse=True)
async def finish_jobs():
    yield
    await jobs.wait_all(max_seconds=10)


# ---------------------------------------------------------------- trainee's card → scenario


async def test_student_card_becomes_scenario_and_feeds_student_made_sessions(
    client: AsyncClient,
) -> None:
    from tests.api.test_call_intake import (
        GAS_PIPE,
        GAS_PIPE_CARD,
        call_session,
        current_call,
        submit,
    )
    from tests.api.test_sessions import create_session

    teacher_token = await login(client, "teacher1")
    headers = bearer(teacher_token)
    lesson = await call_session(client, teacher_token, GAS_PIPE)
    student = await login(client, "student1")
    attempt_id = (await current_call(client, student, lesson["id"]))["attempt_id"]

    # Before the trainee saves the card there is nothing to turn into a scenario.
    r = await client.post(f"/api/scenarios/from-attempt/{attempt_id}", headers=headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "card_not_submitted"
    r = await submit(client, student, attempt_id, GAS_PIPE_CARD, "sub-scn")
    assert r.status_code == 200, r.text

    r = await client.post(f"/api/scenarios/from-attempt/{attempt_id}", headers=headers)
    assert r.status_code == 201, r.text
    scenario = r.json()
    assert scenario["kind"] == "card_response"
    assert scenario["source"] == "student" and scenario["status"] == "review"
    assert scenario["title"].endswith("карточка обучающегося")
    assert scenario["ticket_ref"] == "31-3"
    assert scenario["problems"] == []
    card = scenario["body"]["card"]
    assert card["incident_type"] == "13.2.4.0"
    assert card["signs"] == ["Запах газа в помещении", "От газововго оборудования"]
    assert card["address"]["street"] == "улица Вавилова" and "object" not in card["address"]
    assert card["caller"]["name"] == "Петров Иван Сергеевич"
    assert card["description"].startswith("Свист на газовой трубе")
    assert scenario["service_code"] in {"mosgaz", "mosoblgaz", "mayor_office", "territorial_oiv"}
    assert card["notified"][-1] == {"service": scenario["service_code"], "status": "Добавлена"}
    assert scenario["body"]["reference"]["decision"] == "accept"
    assert scenario["body"]["student"]["login"] == "student1"

    # The same attempt again returns the same scenario instead of a duplicate.
    r = await client.post(f"/api/scenarios/from-attempt/{attempt_id}", headers=headers)
    assert r.status_code == 200 and r.json()["id"] == scenario["id"]
    other = bearer(await login(client, "teacher2"))
    r = await client.post(f"/api/scenarios/from-attempt/{attempt_id}", headers=other)
    assert r.status_code == 404

    r = await client.get("/api/scenarios", headers=headers, params={"source": "student"})
    assert scenario["id"] in {i["id"] for i in r.json()["items"]}

    # A card_response lesson from trainees' cards has nothing until the scenario is approved.
    draft = await create_session(
        client,
        teacher_token,
        mode="card_response",
        card_source="student_made",
        service_profile=[scenario["service_code"]],
    )
    assert draft["card_source"] == "student_made"
    assert scenario["id"] not in {q["id"] for q in draft["queue"]}

    r = await client.post(
        f"/api/scenarios/{scenario['id']}/approve", headers=headers, json={"confirm_grammar": True}
    )
    assert r.status_code == 200 and r.json()["status"] == "approved"
    r = await client.get(f"/api/sessions/{draft['id']}", headers=headers)
    assert {q["id"] for q in r.json()["queue"]} == {scenario["id"]}

    # «generated» excludes trainees' cards; an unknown source is refused.
    generated = await create_session(
        client,
        teacher_token,
        mode="card_response",
        card_source="generated",
        service_profile=[scenario["service_code"]],
    )
    assert scenario["id"] not in {q["id"] for q in generated["queue"]}
    r = await client.post(
        "/api/sessions",
        headers=headers,
        json={
            "title": "x",
            "mode": "card_response",
            "group_id": draft["group_id"],
            "card_source": "bogus",
            "difficulty": 1,
            "service_profile": [],
            "norm_seconds": 30,
            "pass_threshold": 70,
        },
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_card_source"


# ---------------------------------------------------------------- delete, archive, restore


async def test_unused_scenario_is_deleted_for_good(client: AsyncClient) -> None:
    headers = await teacher(client)
    created = await create_draft(client, headers)
    scenario_id = created["id"]

    r = await client.delete(f"/api/scenarios/{scenario_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"result": "deleted"}
    r = await client.get(f"/api/scenarios/{scenario_id}", headers=headers)
    assert r.status_code == 404
    r = await client.get("/api/scenarios", headers=headers, params={"q": "Тестовый сценарий"})
    assert scenario_id not in [i["id"] for i in r.json()["items"]]

    async with SessionLocal() as session:
        entry = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "scenario.deleted", AuditLog.entity_id == scenario_id
            )
        )
    assert entry is not None and entry.details["attempts"] == 0


async def test_used_scenario_is_archived_hidden_and_restorable(client: AsyncClient) -> None:
    from tests.api.test_call_intake import GAS_PIPE, call_session, current_call
    from tests.api.test_sessions import group_id

    teacher_token = await login(client, "teacher1")
    headers = bearer(teacher_token)
    lesson = await call_session(client, teacher_token, GAS_PIPE)
    student = await login(client, "student1")
    attempt_id = (await current_call(client, student, lesson["id"]))["attempt_id"]
    async with SessionLocal() as session:
        scenario_id = str(
            await session.scalar(select(Scenario.id).where(Scenario.seed_key == GAS_PIPE))
        )

    # Deleting a scenario a trainee has worked on archives it: the attempt keeps its reference.
    r = await client.delete(f"/api/scenarios/{scenario_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"result": "archived"}
    r = await client.get(f"/api/scenarios/{scenario_id}", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "archived"
    r = await client.get(f"/api/attempts/{attempt_id}", headers=bearer(student))
    assert r.status_code == 200

    # Hidden from the default list, visible under the «archived» filter.
    listed = (await client.get("/api/scenarios", headers=headers)).json()["items"]
    assert scenario_id not in [i["id"] for i in listed]
    archived = (
        await client.get("/api/scenarios", headers=headers, params={"status": "archived"})
    ).json()["items"]
    assert scenario_id in [i["id"] for i in archived]

    # No edits while archived; a new lesson cannot queue it.
    r = await client.put(
        f"/api/scenarios/{scenario_id}", headers=headers, json={"body": {"title": "x"}}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "scenario_archived"
    r = await client.post(
        "/api/sessions",
        headers=headers,
        json={
            "title": "Повтор",
            "group_id": await group_id(),
            "mode": "call_intake",
            "difficulty": 1,
            "service_profile": [],
            "norm_seconds": 90,
            "scenario_ids": [scenario_id],
            "dialog_mode": "select",
            "voice_enabled": False,
        },
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_scenario"

    # Restore: the seed scenario is fully approved, so it comes back approved.
    r = await client.post(f"/api/scenarios/{scenario_id}/restore", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    r = await client.post(f"/api/scenarios/{scenario_id}/restore", headers=headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_archived"


async def test_student_cannot_delete_scenarios(client: AsyncClient) -> None:
    headers = await teacher(client)
    created = await create_draft(client, headers)
    student = bearer(await login(client, "student1"))
    r = await client.delete(f"/api/scenarios/{created['id']}", headers=student)
    assert r.status_code == 403
