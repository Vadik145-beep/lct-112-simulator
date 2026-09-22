"""GenerationProvider with a scripted model: schema-constrained answer becomes a valid body,
an invalid answer is retried and then falls back to the template, an unreachable model falls
back at once. Retrieval index on TF-IDF."""

from __future__ import annotations

import pytest

from app.domain.evaluation.schemas import parse_scenario
from app.domain.scenarios.facts import parse_ticket
from app.domain.scenarios.template import ServiceInfo
from app.providers.embeddings import TfidfEmbedding
from app.providers.generation import (
    JSON_RETRIES,
    GenerationContext,
    GenerationRequest,
    LlmGeneration,
    TemplateGeneration,
    build_generation_provider,
    candidate_rows,
)
from app.providers.llm import ModelUnavailableError
from app.providers.rag import Chunk, ReferenceIndex, split_document
from tests.providers.fake_model import FakeModel

ROWS = [
    {
        "code": "1.1.1.1",
        "group_code": "1",
        "sign1": "на улице",
        "sign2": "мусор",
        "sign3": "открытое пламя",
        "final_title": "пожар: мусор",
        "main_service": "101",
        "service_rules": [{"service": "101", "when": [], "notify": True}],
        "flag_codes": ["injured"],
    },
    {
        "code": "1.99.0.8",
        "group_code": "1",
        "sign1": "Не отображается оператору 112",
        "final_title": "Пожар: Автостоянка",
        "main_service": "101",
        "service_rules": [
            {"service": "101", "when": [], "notify": True},
            {"service": "gkh", "when": [], "notify": True},
        ],
        "flag_codes": [],
    },
    {
        "code": "15.10.1.0",
        "group_code": "15",
        "sign1": "Нарушение тишины - в неразрешенное время",
        "sign2": "Шум, музыка",
        "final_title": "Нарушение тишины",
        "main_service": "102",
        "service_rules": [{"service": "102", "when": [], "notify": True}],
        "flag_codes": [],
    },
]
CATALOGUE = {
    "101": ServiceInfo("101", False),
    "102": ServiceInfo("102", False),
    "gkh": ServiceInfo("gkh", True),
    "territorial_oiv": ServiceInfo("territorial_oiv", True),
}
CTX = GenerationContext(
    type_rows=ROWS,
    catalogue=CATALOGUE,
    memo_chunks=[Chunk("Памятка, стр. 21", "Статус «Принята» ставится в течение 30 секунд.")],
)


def model_answer(**overrides) -> dict:
    replies = [
        {"topic": "what_happened", "text": f"Горит машина на паркинге, вариант {i}!"}
        for i in range(6)
    ] + [
        {"topic": "address", "text": "Улица Грина, дом одиннадцать, подземный паркинг."},
        {"topic": "injured", "text": "Никого не видно, дым сильный."},
        {"topic": "danger", "text": "Дым идёт в подъезд!"},
        {"topic": "caller_name", "text": "Я Миша, мне десять лет."},
        {"topic": "callback_phone", "text": "Это мамин телефон."},
        {"topic": "repeat", "text": "Что? Не слышу!"},
        {"topic": "unknown", "text": "Не знаю я!"},
    ]
    answer = {
        "title": "Пожар в подземном паркинге, звонит ребёнок",
        "difficulty": 2,
        "persona": "child",
        "noise": "indoor",
        "opening": "Алло, тётя, у нас внизу машина горит!",
        "facts": {
            "what_happened": "горит автомобиль в подземном паркинге",
            "address": "ул. Грина, 11",
        },
        "behaviour": "ребёнок, адрес называет со второго раза",
        "drops_call": False,
        "replies": replies,
        "required_topics": ["what_happened", "address", "injured", "caller_name"],
        "incident_type": "1.99.0.8",
        "flags": {"injured": False},
        "address": {"street": "улица Грина", "house": "11"},
        "caller": {"name": "Миша", "role": "очевидец", "phone": None},
        "description": "Горит автомобиль в подземном паркинге жилого дома, дым идёт в подъезд.",
        "description_keywords": ["паркинг", "автомобиль", "дым"],
    }
    answer.update(overrides)
    return answer


def request(**overrides) -> GenerationRequest:
    base = {"kind": "call_intake", "phrase": "пожар в подземном паркинге, звонит ребёнок"}
    base.update(overrides)
    return GenerationRequest(**base)


async def test_model_answer_becomes_a_body_with_reference_from_the_row() -> None:
    fake = FakeModel([model_answer()])
    result = await LlmGeneration(fake).generate(request(), CTX)
    assert result.method == "llm" and result.attempts == 1
    scenario = parse_scenario(result.body)
    assert scenario.kind == "call_intake"
    assert scenario.caller.persona == "child" and scenario.caller.voice == "ru_child_1"
    assert scenario.reference_card.incident_type == "1.99.0.8"
    assert scenario.reference_card.signs_path == ["Не отображается оператору 112"]
    assert scenario.reference_card.expected_services == [
        "101",
        "gkh",
    ]  # from the row, not the model
    assert [r.id for r in scenario.replies] == list(range(1, 14))
    assert not any(r.approved for r in scenario.replies)
    schema = fake.calls[0]["schema"]
    assert set(schema["properties"]["incident_type"]["enum"]) <= {r["code"] for r in ROWS}
    prompt = fake.calls[0]["messages"][1]["content"]
    assert "Памятка, стр. 21" in prompt and "1.99.0.8" in prompt


async def test_card_of_a_ticket_keeps_the_ticket_address_and_voice() -> None:
    """From a ticket the reference card is graded against the ticket, not against what the
    model wrote: an invented street is not in the card's hint list, so the trainee could not
    fill it. The voice follows the caller of the ticket, a man is not voiced as a woman."""
    facts = parse_ticket(
        "Задымление на минус первом этаже в торговом центре, "
        "Ивлев Артем Олегович (работник мебельного магазина), 916-123-98-78",
        "Москва, станция метро Пражская, ТЦ «Электронный рай» (ул. Кировоградская, дом 15)",
    )
    answer = model_answer(
        persona="worried_resident",
        address={"street": "улица Придуманная", "house": "7"},
        caller={"name": "Кто-то", "role": "очевидец", "phone": "000"},
    )
    result = await LlmGeneration(FakeModel([answer])).generate(
        request(facts=facts, ticket_ref="8-1", phrase=None), CTX
    )
    scenario = parse_scenario(result.body)
    assert scenario.reference_card.address.street == "ул. Кировоградская"
    assert scenario.reference_card.address.house == "15"
    assert scenario.reference_card.caller.name == "Ивлев Артем Олегович"
    assert scenario.reference_card.caller.phone == "916-123-98-78"
    # worried_resident speaks in ru_female_1; the caller of the ticket is a man.
    assert scenario.caller.voice == "ru_male_1"


async def test_invalid_answers_are_retried_then_template() -> None:
    fake = FakeModel(
        ["not json at all", model_answer(persona="alien"), model_answer(incident_type="7.7.7.7")]
    )
    result = await LlmGeneration(fake).generate(request(), CTX)
    assert result.method == "template"
    assert result.attempts == JSON_RETRIES + 1
    assert result.note and "шаблон" in result.note
    assert len(fake.calls) == JSON_RETRIES + 1
    # The retry carries the validation error back to the model.
    assert "не прошёл проверку" in fake.calls[1]["messages"][-1]["content"]
    parse_scenario(result.body)


async def test_unavailable_model_falls_back_at_once() -> None:
    fake = FakeModel([ModelUnavailableError("down")])
    result = await LlmGeneration(fake).generate(request(), CTX)
    assert result.method == "template" and result.attempts == 1
    assert "недоступна" in (result.note or "")
    assert parse_scenario(result.body).title


async def test_card_response_from_model() -> None:
    fake = FakeModel(
        [
            {
                "title": "Пожар на автостоянке",
                "difficulty": 1,
                "incident_type": "1.99.0.8",
                "flags": {"injured": False},
                "address": {"street": "улица Грина", "house": "11"},
                "caller": {"name": "Миша", "role": "очевидец"},
                "description": "Горит автомобиль на подземной автостоянке.",
                "decision": "accept",
                "reject_reason": None,
                "comments": ["Направлен дежурный, наряд 12", "Работы ведутся", "Работы завершены"],
            }
        ]
    )
    result = await LlmGeneration(fake).generate(request(kind="card_response"), CTX)
    scenario = parse_scenario(result.body)
    assert scenario.kind == "card_response"
    assert scenario.service == "gkh"  # first notified service on АРМ-112
    assert [n.service for n in scenario.card.notified] == ["101", "gkh"]
    assert scenario.reference.status_chain[1].comment_example == "Направлен дежурный, наряд 12"


async def test_template_provider_honours_teacher_choices() -> None:
    result = await TemplateGeneration().generate(
        request(incident_type="15.10.1.0", persona="elderly_calm", noise="crowd", difficulty=3), CTX
    )
    scenario = parse_scenario(result.body)
    assert scenario.reference_card.incident_type == "15.10.1.0"
    assert scenario.caller.persona == "elderly_calm"
    assert scenario.caller.noise == "crowd"
    assert scenario.difficulty == 3
    assert result.latency_ms < 2000


def test_candidate_rows_put_teacher_type_first_and_respect_group() -> None:
    rows = candidate_rows(request(incident_type="15.10.1.0"), ROWS)
    assert rows[0]["code"] == "15.10.1.0"
    rows = candidate_rows(request(incident_group="1"), ROWS)
    assert {r["group_code"] for r in rows} == {"1"}


def test_provider_factory() -> None:
    assert build_generation_provider(None).method == "template"
    assert build_generation_provider("http://llm-gen:8080").method == "llm"


def test_split_document_and_tfidf_index(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    text = "Первый абзац о статусе «Принята» и тридцати секундах.\n\n" + "Длинный абзац. " * 80
    chunks = split_document(text)
    assert len(chunks) >= 2 and all(len(c) <= 700 for c in chunks)

    index = ReferenceIndex(TfidfEmbedding())
    index.set_tickets(
        [
            ("1-1", "Возгорание мусорного контейнера, пострадавших нет"),
            ("2-2", "Поругался с продавцом Мегафон, бросил трубку"),
            ("3-2", "Громко играет музыка во дворе"),
        ]
    )
    hits = index.similar_tickets("скандал в магазине с продавцом", limit=1)
    assert hits[0].source == "Билет 2-2"
    index.set_tickets([("1-1", "Возгорание")])  # changed list is re-embedded
    assert index.similar_tickets("возгорание", limit=1)[0].source == "Билет 1-1"


async def test_revision_comment_reaches_the_model_but_not_the_template() -> None:
    fake = FakeModel([model_answer()])
    req = request(comment="Сделай заявителя более растерянным")
    await LlmGeneration(fake).generate(req, CTX)
    assert "более растерянным" in fake.calls[0]["messages"][1]["content"]
    result = await TemplateGeneration().generate(req, CTX)
    assert "растерянным" not in parse_scenario(result.body).caller.facts["what_happened"]
