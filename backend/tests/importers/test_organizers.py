"""Import of the organizers' dataset and the reference endpoints built on it."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.importers import organizers
from tests.conftest import BACKEND_DIR, bearer, login

DATA_DIR = BACKEND_DIR.parent / "data"

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists() and not (DATA_DIR / "organizers").exists(),
    reason="нет data/seed/classifier.json и data/organizers",
)


@pytest.fixture(scope="module")
async def imported() -> dict:
    return await organizers.run(DATA_DIR)


async def test_summary_counts(imported: dict) -> None:
    assert imported["groups"] == 24
    assert imported["types"] == 1283
    assert imported["types_in_db"] == 1283
    assert imported["services"] == 64
    assert imported["flags"] == 16
    assert imported["response_statuses"] == 9
    assert imported["card_statuses"] == 7
    assert imported["tickets"] == 96
    assert imported["unparsed_columns"] == 0
    assert imported["warnings"] == 0


async def test_import_is_idempotent(imported: dict, admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as conn:
        before = (
            await conn.execute(text("SELECT id FROM tickets ORDER BY ticket_no, item_no"))
        ).all()
        types_before = await conn.scalar(text("SELECT count(*) FROM incident_types"))
    again = await organizers.run(DATA_DIR)
    assert again["types_in_db"] == types_before == 1283
    async with admin_engine.connect() as conn:
        after = (
            await conn.execute(text("SELECT id FROM tickets ORDER BY ticket_no, item_no"))
        ).all()
        streets_dupes = await conn.scalar(
            text("SELECT count(*) - count(DISTINCT (name, okrug, district)) FROM streets")
        )
    assert after == before  # ticket ids survive a re-import (scenarios will reference them)
    assert streets_dupes == 0


async def test_reference_endpoints_require_login(client: AsyncClient) -> None:
    r = await client.get("/api/classifier/tree")
    assert r.status_code == 401


async def test_classifier_tree(imported: dict, client: AsyncClient) -> None:
    token = await login(client, "student1")
    r = await client.get("/api/classifier/tree", headers=bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert body["types_total"] == 1283
    assert [g["title"] for g in body["groups"]][:2] == ["Пожары и задымления", "ДТП"]
    fires = body["groups"][0]["children"]
    street = next(n for n in fires if n["title"] == "на улице")
    garbage = next(n for n in street["children"] if n["title"] == "мусор")
    flame = next(n for n in garbage["children"] if n["title"] == "открытое пламя")
    assert flame["type_code"] == "1.1.1.1"
    assert flame["final_title"] == "пожар: мусор"
    # «ДТП» is both a type (без пострадавших) and a folder for vehicle kinds.
    dtp = next(n for n in body["groups"][1]["children"] if n["title"] == "ДТП")
    assert dtp["type_code"] == "2.1.0.0"
    assert any(c["title"] == "Транспорт легковой" for c in dtp["children"])


async def test_services_by_type_and_flags(imported: dict, client: AsyncClient) -> None:
    token = await login(client, "student1")
    r = await client.get("/api/classifier/1.1.1.1/services", headers=bearer(token))
    assert r.status_code == 200
    plain = r.json()
    codes = [s["code"] for s in plain["services"]]
    assert "101" in codes
    assert "103" not in codes
    assert plain["main_service"] == "101"
    assert "injured" in plain["available_flags"]

    r = await client.get(
        "/api/classifier/1.1.1.1/services", params={"flags": "injured"}, headers=bearer(token)
    )
    with_injured = [s["code"] for s in r.json()["services"]]
    assert {"101", "102", "103", "cemp"} <= set(with_injured)

    r = await client.get(
        "/api/classifier/1.1.1.1/services",
        params={"flags": "injured,not_on_site"},
        headers=bearer(token),
    )
    assert "103" not in [s["code"] for s in r.json()["services"]]

    r = await client.get(
        "/api/classifier/1.1.1.1/services", params={"flags": "bogus"}, headers=bearer(token)
    )
    assert r.status_code == 422
    r = await client.get("/api/classifier/0.0.0.0/services", headers=bearer(token))
    assert r.status_code == 404


async def test_statuses_are_quoted_from_memo(imported: dict, client: AsyncClient) -> None:
    token = await login(client, "teacher1")
    r = await client.get("/api/response-statuses", headers=bearer(token))
    titles = [s["title"] for s in r.json()]
    assert titles == [
        "Добавлена",
        "Получена службой",
        "Принята",
        "Не принята",
        "Начало реагирования",
        "Прибытие",
        "Проведение работ",
        "Работы завершены",
        "Отказ от выполнения работ",
    ]
    by_code = {s["code"]: s for s in r.json()}
    assert by_code["rejected"]["requires_comment"] is True
    assert by_code["rejected"]["allowed_next"] == ["accepted"]
    assert by_code["response_started"]["requires_order_number"] is True
    assert by_code["works_done"]["is_final"] is True

    r = await client.get("/api/card-statuses", headers=bearer(token))
    cards = r.json()
    assert [c["title"] for c in cards] == [
        "Зарегистрирована",
        "Отработана",
        "Проверена",
        "Не оповещено",
        "Отказ",
        "Не завершено",
        "Завершена",
    ]
    assert [c["title"] for c in cards if c["is_alert"]] == ["Не оповещено", "Отказ", "Не завершено"]


async def test_services_flags_reasons_errors_topics(imported: dict, client: AsyncClient) -> None:
    token = await login(client, "student1")
    services = (await client.get("/api/services", headers=bearer(token))).json()
    by_code = {s["code"]: s for s in services}
    assert by_code["103"]["no_reject"] is True
    assert by_code["103"]["via_arm112"] is False
    assert by_code["territorial_oiv"]["via_arm112"] is True

    flags = (await client.get("/api/incident-flags", headers=bearer(token))).json()
    assert {"injured", "no_access", "threat", "offense", "not_on_site"} <= {
        f["code"] for f in flags
    }

    reasons = (await client.get("/api/reject-reasons", headers=bearer(token))).json()
    assert "Дубль" in [x["title"] for x in reasons]

    errors = (
        await client.get(
            "/api/typical-errors", params={"mode": "card_response"}, headers=bearer(token)
        )
    ).json()
    codes = {e["code"] for e in errors}
    assert {
        "status_mismatch",
        "competence_refusal",
        "late_primary",
        "no_status",
        "empty_reject_comment",
        "incomplete_comment",
        "progress_missing",
        "profile_refusal",
        "duplicate_accepted",
        "wrong_accept_unfixed",
    } <= codes
    assert all(e["mode"] == "card_response" and e["example"] for e in errors)

    topics = (await client.get("/api/caller-topics", headers=bearer(token))).json()
    assert {"address", "what_happened", "injured", "caller_name"} <= {t["code"] for t in topics}


async def test_tickets(imported: dict, client: AsyncClient) -> None:
    token = await login(client, "student1")
    tickets = (await client.get("/api/tickets", headers=bearer(token))).json()
    assert len(tickets) == 96
    assert {(t["ticket_no"], t["item_no"]) for t in tickets} == {
        (n, i) for n in range(1, 33) for i in (1, 2, 3)
    }
    first = tickets[0]
    assert first["situation"].startswith("Возгорание мусорного контейнера")
    assert "descriptive_address" in first["traps"]
    other_region = next(t for t in tickets if t["ticket_no"] == 1 and t["item_no"] == 3)
    assert "other_region" in other_region["traps"]
    dropped = next(t for t in tickets if t["ticket_no"] == 2 and t["item_no"] == 2)
    assert "call_dropped" in dropped["traps"]
    assert sum(1 for t in tickets if not t["ocr_confident"]) <= 10


async def test_street_hint(imported: dict, client: AsyncClient) -> None:
    if imported["streets"] == 0:
        pytest.skip("нет data/seed/streets.json")
    token = await login(client, "student1")
    r = await client.get("/api/streets", params={"q": "берзар"}, headers=bearer(token))
    assert r.status_code == 200
    hits = r.json()
    assert hits, "улица Берзарина не найдена"
    berzarina = next(h for h in hits if "Берзарина" in h["name"])
    assert berzarina["okrug"] == "СЗАО"
    assert berzarina["district"]
    # «ё» and case do not matter: «Хорошёвское шоссе».
    r = await client.get("/api/streets", params={"q": "ХОРОШЕВСК"}, headers=bearer(token))
    assert any("Хорош" in h["name"] for h in r.json())
    r = await client.get("/api/streets", params={"q": "x"}, headers=bearer(token))
    assert r.status_code == 422


def test_detect_traps() -> None:
    assert organizers.detect_traps(
        "Поругался с продавцом, бросил трубку", "Москва, Сущёвский Вал, 5"
    ) == ["call_dropped"]
    traps = organizers.detect_traps(
        "Ребенок 11 лет упал с велосипеда, отек руки. Вызывает мама",
        "Волгоградская обл., г. Волжский, ул. Карла Маркса",
    )
    assert {"other_region", "child", "caller_not_victim"} <= set(traps)
    assert "other_region" not in organizers.detect_traps(
        "Задымление", "Москва, ул. Берзарина, дом 21, корп. 1, под. 3, домофон 68"
    )
