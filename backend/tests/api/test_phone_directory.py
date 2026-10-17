"""The phone directory and the virtual phone (#263): the teacher keeps up to ten numbers per
service with the customer's four roles; the trainee dials a number and the right person
answers; the caller of the card is reachable only when the lesson allows it."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.dialog import officer
from app.domain.scenarios import staff
from app.models import MAX_PHONE_ENTRIES, MODE_CARD_RESPONSE
from tests.api.conftest import DATA_DIR
from tests.api.test_caller_callback import CARD, set_caller_phone
from tests.api.test_dialog import make_attempt
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)

SERVICE = "moek"  # the service of CARD


async def directory(client: AsyncClient, token: dict) -> dict:
    r = await client.get("/api/phone-directory", headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()


def numbers_of(data: dict, service: str = SERVICE) -> dict[str, str]:
    """Role → number of the service's default entries."""
    return {e["role"]: e["number"] for e in data["entries"] if e["service_code"] == service}


async def add(client: AsyncClient, token: dict, **body):
    payload = {"service_code": SERVICE, "role": staff.ROLE_SQUAD_SPECIALIST, "name": "Иванов"}
    return await client.post("/api/phone-directory", headers=bearer(token), json=payload | body)


async def dial(client: AsyncClient, token: dict, attempt_id: uuid.UUID, number: str):
    return await client.post(
        f"/api/attempts/{attempt_id}/dial", headers=bearer(token), json={"number": number}
    )


async def test_every_service_starts_with_the_four_roles(client: AsyncClient) -> None:
    data = await directory(client, await login(client, "teacher1"))
    assert data["max_per_service"] == MAX_PHONE_ENTRIES
    assert [r["code"] for r in data["roles"]] == list(staff.ROLES)
    own = numbers_of(data)
    assert set(own) == set(staff.ROLES)
    assert all(len(n) == 4 and n.isdigit() for n in own.values())


async def test_teacher_edits_within_the_rules(client: AsyncClient) -> None:
    token = await login(client, "teacher1")
    taken = numbers_of(await directory(client, token))[staff.ROLE_HEAD]
    added: list[str] = []
    try:
        bad_role = await add(client, token, role="dispatcher", number="9001")
        assert bad_role.status_code == 422
        assert bad_role.json()["error"]["code"] == "unknown_role"
        bad_number = await add(client, token, number="12")
        assert bad_number.status_code == 422
        assert bad_number.json()["error"]["code"] == "bad_number"
        twice = await add(client, token, number=taken)
        assert twice.status_code == 409
        assert twice.json()["error"]["code"] == "number_taken"

        free = MAX_PHONE_ENTRIES - len(numbers_of(await directory(client, token)))
        for i in range(free):
            r = await add(client, token, number=f"90{i:02d}")
            assert r.status_code == 201, r.text
            added.append(r.json()["id"])
        full = await add(client, token, number="9099")
        assert full.status_code == 409
        assert full.json()["error"]["code"] == "directory_full"

        changed = await client.patch(
            f"/api/phone-directory/{added[0]}",
            headers=bearer(token),
            json={"name": "Петров П. П.", "role": staff.ROLE_RESPONSE_CHIEF},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["role_title"] == "Начальник отдела реагирования"
        assert changed.json()["name"] == "Петров П. П."
    finally:
        for entry_id in added:
            gone = await client.delete(f"/api/phone-directory/{entry_id}", headers=bearer(token))
            assert gone.status_code == 204


async def test_trainee_cannot_edit_the_directory(client: AsyncClient) -> None:
    token = await login(client, "student1")
    assert (await client.get("/api/phone-directory", headers=bearer(token))).status_code == 403
    assert (await add(client, token, number="9050")).status_code == 403


async def test_phone_book_shows_the_caller_line_only_when_allowed(client: AsyncClient) -> None:
    token = await login(client, "student1")
    for allowed in (False, True):
        attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, allow_caller_call=allowed)
        r = await client.get(f"/api/attempts/{attempt_id}/phone-book", headers=bearer(token))
        assert r.status_code == 200, r.text
        book = r.json()
        assert book["service"] == SERVICE
        roles = [e["role"] for e in book["entries"]]
        assert set(staff.ROLES) <= set(roles)
        assert ("caller" in roles) is allowed
        # The caller's number is never in the book: the trainee dials what the card shows.
        assert all(e["number"] is None for e in book["entries"] if e["role"] == "caller")


async def test_dialled_number_tells_who_answers(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    numbers = numbers_of(await directory(client, teacher))
    token = await login(client, "student1")

    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE)
    chief = await dial(client, token, attempt_id, numbers[staff.ROLE_SQUAD_CHIEF])
    assert chief.status_code == 200, chief.text
    call = chief.json()["call"]
    # The squad chief is the officer of the own service's tab.
    assert call["kind"] == officer.KIND_OUTGOING
    assert call["service"] == SERVICE
    await client.post(
        f"/api/attempts/{attempt_id}/service-call/{call['id']}/end", headers=bearer(token)
    )

    head = await dial(client, token, attempt_id, numbers[staff.ROLE_HEAD])
    assert head.status_code == 200, head.text
    call = head.json()["call"]
    assert call["kind"] == officer.KIND_STAFF
    assert call["role"] == staff.ROLE_HEAD
    assert call["role_title"] == "Руководитель организации"
    assert call["turns"][0]["text"] == "Руководитель МОЭК, слушаю."
    assert call["facts_required"] == []
    await client.post(
        f"/api/attempts/{attempt_id}/service-call/{call['id']}/end", headers=bearer(token)
    )

    missing = await dial(client, token, attempt_id, "0000")
    assert missing.status_code == 404
    assert missing.json()["error"]["message"] == "Набранный номер не существует."
    empty = await dial(client, token, attempt_id, "  ")
    assert empty.status_code == 422


async def test_caller_answers_only_when_the_lesson_allows(client: AsyncClient) -> None:
    was = await set_caller_phone("+7 (916) 123-45-67")
    try:
        token = await login(client, "student1")
        closed = await make_attempt(CARD, mode=MODE_CARD_RESPONSE)
        r = await dial(client, token, closed, "89161234567")
        assert r.status_code == 409
        assert r.json()["error"]["message"] == "Номер недоступен."
        # The handset of «АОН» in the card follows the same permission.
        handset = await client.post(
            f"/api/attempts/{closed}/service-call",
            headers=bearer(token),
            json={"service": officer.CALLER_TARGET},
        )
        assert handset.status_code == 409
        assert handset.json()["error"]["code"] == "number_unavailable"

        open_ = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, allow_caller_call=True)
        r = await dial(client, token, open_, "8 916 123 45 67")
        assert r.status_code == 200, r.text
        assert r.json()["call"]["kind"] == officer.KIND_CALLER
    finally:
        await set_caller_phone(was)
