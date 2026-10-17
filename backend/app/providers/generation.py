"""GenerationProvider: a scenario draft from a ticket or a teacher's phrase (PRD 9.6).

* ``LlmGeneration`` — the ``llm-gen`` server (llama.cpp, 7B) answers by the JSON schema of
  ``app.domain.scenarios.generated`` with the incident-type candidates as an enum, so codes
  cannot be invented; the answer is validated by Pydantic and retried up to ``JSON_RETRIES``
  times with the validation error in the conversation. The reference card (signs, services)
  is then derived from the chosen classifier row, never from the model.
* ``TemplateGeneration`` — no model: ``app.domain.scenarios.template`` fills a draft from the
  parsed facts; also the fallback when the model server is down.

Both are pure with respect to the database: the caller passes the classifier candidates, the
service catalogue and the retrieved reference chunks in ``GenerationContext``.
"""

from __future__ import annotations

import dataclasses
import json
import random
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.domain.evaluation.schemas import Address
from app.domain.scenarios import distort, examples, template
from app.domain.scenarios.classify import rank_types
from app.domain.scenarios.facts import TicketFacts, parse_ticket
from app.domain.scenarios.generated import (
    GeneratedCallIntake,
    GeneratedCardResponse,
    call_intake_schema,
    card_response_schema,
)
from app.domain.scenarios.personas import NOISES, PERSONAS, persona, voice_for
from app.domain.services import resolve_services
from app.logging import get_logger
from app.providers.llm import (
    ChatModel,
    LlamaCppChat,
    Message,
    ModelOutputError,
    ModelUnavailableError,
    OpenAiCompatibleChat,
)
from app.providers.rag import Chunk

log = get_logger(__name__)

JSON_RETRIES = 2  # PRD 9.6: up to two more tries on an invalid answer
GENERATION_MAX_TOKENS = 2200
GENERATION_TIMEOUT_SECONDS = 900.0  # default; the stand sets LLM_GEN_TIMEOUT_SECONDS
GENERATION_TEMPERATURE = 0.4
TYPE_CANDIDATES = 12  # classifier rows offered to the model
HOUSE_NUMBERS = (1, 120)  # a house picked for a card without a ticket address
ROAD_KILOMETRE = "километр"  # «МКАД, 46-й километр» in the street list


@dataclass
class GenerationRequest:
    kind: str  # call_intake | card_response
    phrase: str | None = None  # the teacher's phrase («пожар в подземном паркинге, звонит ребёнок»)
    facts: TicketFacts | None = None  # parsed ticket, when generating from a ticket
    ticket_ref: str | None = None
    traps: list[str] = field(default_factory=list)
    incident_type: str | None = None  # type chosen by the teacher, if any
    incident_group: str | None = None  # group chosen by the teacher, narrows the candidates
    difficulty: int | None = None
    persona: str | None = None
    noise: str | None = None
    # Teacher's remark for a revision («Переделать»); the model reads it, the template ignores it.
    comment: str | None = None
    # A real address for a card without a ticket (a street of the reference list); the model
    # writes the description around it and the card keeps it, so nothing is invented.
    address: Address | None = None
    # Kinds of operator mistakes to plant first (``distort``): the trainee's weak spots.
    mistakes_first: list[str] = field(default_factory=list)
    # The service the card is for (the trainee's own); empty = the first notified on АРМ-112.
    service: str | None = None
    # The card was sent to ``service`` though the incident is not its concern (issue #265):
    # the right answer is «Не принята», and it is one of the card's planted mistakes.
    wrong_service: bool = False

    def facts_or_phrase(self) -> TicketFacts:
        if self.facts is not None:
            return self.facts
        facts = parse_ticket(self.phrase or "", "")
        if self.address is None:
            return facts
        return dataclasses.replace(
            facts, address=self.address, address_text=address_text(self.address)
        )


@dataclass
class GenerationContext:
    type_rows: Sequence[Mapping]  # classifier rows the type may be chosen from
    catalogue: Mapping[str, template.ServiceInfo]
    memo_chunks: Sequence[Chunk] = ()
    similar_tickets: Sequence[Chunk] = ()
    # Street names for look-alike street mistakes (issue #35); empty = that kind is skipped.
    streets: Sequence[str] = ()
    # Services a card may be wrongly sent to (the lesson's); empty = any on АРМ-112.
    services: Sequence[str] = ()


def address_text(address: Address) -> str:
    return ", ".join(
        part for part in (address.street, f"дом {address.house}" if address.house else None) if part
    )


def with_real_address(request: GenerationRequest, ctx: GenerationContext) -> GenerationRequest:
    """A card from a phrase gets a real street and a house (stable per request); a ticket
    keeps its own address."""
    if request.facts is not None or request.address is not None or not ctx.streets:
        return request
    rng = random.Random(  # noqa: S311 - a stable choice per request, not security
        f"{request.phrase}|{request.ticket_ref}|{request.incident_type}|{request.difficulty}"
    )
    # Kilometres of ring roads and highways have no houses.
    streets = sorted(s for s in ctx.streets if ROAD_KILOMETRE not in s.lower()) or sorted(
        ctx.streets
    )
    street = rng.choice(streets)
    house = str(rng.randint(*HOUSE_NUMBERS))
    return dataclasses.replace(request, address=Address(street=street, house=house))


def plant_mistakes(
    body: dict, ctx: GenerationContext, request: GenerationRequest | None = None
) -> dict:
    """Operator mistakes for a card by its difficulty (issues #35, #265, ``distort``); a card
    planned as sent to the wrong service carries that mistake first."""
    if request is not None and request.wrong_service:
        body = distort.sent_to_wrong_service(body, ctx.catalogue)
    # A card for the trainee's own service keeps it as its own tab: no other service is planted.
    own = request.service if request is not None and request.service in ctx.catalogue else None
    return distort.inject_by_difficulty(
        body,
        type_rows=ctx.type_rows,
        catalogue=ctx.catalogue,
        streets=ctx.streets,
        services=[own] if own else ctx.services,
        prefer=request.mistakes_first if request is not None else (),
    )


@dataclass
class GenerationResult:
    body: dict
    method: str  # llm | template
    model: str | None = None
    attempts: int = 1
    latency_ms: int = 0
    note: str | None = None  # why the fallback was used


class GenerationProvider(Protocol):
    method: str

    async def generate(
        self, request: GenerationRequest, ctx: GenerationContext
    ) -> GenerationResult: ...

    async def available(self) -> bool: ...


# --- helpers shared by both providers ----------------------------------------------------------


def choose_row(request: GenerationRequest, rows: Sequence[Mapping]) -> Mapping:
    """The classifier row for the draft: the teacher's choice, otherwise the best keyword
    match of the situation among the rows."""
    by_code = {str(r["code"]): r for r in rows}
    if request.incident_type and request.incident_type in by_code:
        return by_code[request.incident_type]
    facts = request.facts_or_phrase()
    address = facts.address
    # A phrase without an address says nothing about indoors/outdoors.
    indoors = (
        True
        if any([address.entrance, address.floor, address.apartment])
        else (False if address.house or address.street else None)
    )
    injured = (
        True if facts.injured.startswith("есть") else (False if facts.injured == "нет" else None)
    )
    ranked = rank_types(
        facts.what_happened or facts.situation, rows, injured=injured, indoors=indoors, limit=1
    )
    if ranked:
        return by_code[ranked[0].code]
    return next(iter(rows))


def candidate_rows(
    request: GenerationRequest, rows: Sequence[Mapping], limit: int = TYPE_CANDIDATES
) -> list[Mapping]:
    """Rows offered to the model: the teacher's type first, then the best keyword matches
    (within the chosen group when there is one)."""
    pool = [
        r
        for r in rows
        if not request.incident_group or str(r.get("group_code")) == request.incident_group
    ] or list(rows)
    facts = request.facts_or_phrase()
    ranked = rank_types(facts.what_happened or facts.situation, pool, limit=limit)
    by_code = {str(r["code"]): r for r in pool}
    chosen: list[Mapping] = []
    if request.incident_type and request.incident_type in by_code:
        chosen.append(by_code[request.incident_type])
    for candidate in ranked:
        row = by_code[candidate.code]
        if row not in chosen:
            chosen.append(row)
    return chosen[:limit] or list(pool)[:limit]


# --- template --------------------------------------------------------------------------------


class TemplateGeneration:
    method = "template"

    async def available(self) -> bool:
        return True

    async def generate(
        self, request: GenerationRequest, ctx: GenerationContext
    ) -> GenerationResult:
        started = time.perf_counter()
        request = with_real_address(request, ctx)
        facts = request.facts_or_phrase()
        row = choose_row(request, ctx.type_rows)
        if request.kind == "call_intake":
            body = template.build_call_intake(
                facts,
                row,
                ticket_ref=request.ticket_ref,
                traps=request.traps,
                persona_code=request.persona,
                noise=request.noise,
                difficulty=request.difficulty,
            )
        else:
            body = template.build_card_response(
                facts,
                row,
                ticket_ref=request.ticket_ref,
                traps=request.traps,
                catalogue=ctx.catalogue,
                difficulty=request.difficulty,
                service=request.service,
            )
            body = plant_mistakes(body, ctx, request)
        return GenerationResult(
            body=body, method=self.method, latency_ms=round((time.perf_counter() - started) * 1000)
        )


# --- model -----------------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Ты методист учебного центра, который готовит сценарии для тренажёра диспетчеров "
    "экстренных служб Москвы (система 112). Пиши по-русски, живым разговорным языком заявителя, "
    "без канцелярита в репликах. Отвечай только JSON по заданной схеме."
)


def _rows_text(rows: Sequence[Mapping]) -> str:
    lines = []
    for row in rows:
        path = " → ".join(str(row[k]) for k in ("sign1", "sign2", "sign3") if row.get(k))
        lines.append(f"- {row['code']}: {path} ({row.get('final_title', '')})")
    return "\n".join(lines)


def _chunks_text(chunks: Sequence[Chunk]) -> str:
    return "\n".join(f"[{c.source}] {c.text}" for c in chunks) or "—"


def _topics_text() -> str:
    from app.domain.reference_data import CALLER_TOPICS

    return ", ".join(f"{t['code']} — {t['title']}" for t in CALLER_TOPICS)


def build_messages(
    request: GenerationRequest, ctx: GenerationContext, rows: Sequence[Mapping]
) -> list[Message]:
    facts = request.facts_or_phrase()
    source = (
        f"Ситуация из экзаменационного билета {request.ticket_ref}: «{facts.situation}». "
        f"Адрес: «{facts.address_text}»."
        if request.facts is not None
        else f"Задание преподавателя: «{request.phrase}»."
        + (
            f" Адрес происшествия: «{address_text(request.address)}» — используй его без "
            "изменений, другой адрес не придумывай."
            if request.address is not None
            else ""
        )
    )
    wishes = []
    if request.difficulty:
        wishes.append(f"сложность {request.difficulty} из 3")
    if request.persona:
        who = persona(request.persona)
        wishes.append(f"персонаж заявителя: {who.title} ({who.style})")
    if request.noise:
        wishes.append(f"фон: {dict(NOISES).get(request.noise, request.noise)}")
    if request.comment:
        wishes.append(f"замечание преподавателя к прошлой версии: {request.comment}")
    if request.traps:
        wishes.append(
            "ловушки ситуации: "
            + ", ".join(template.TRAP_BEHAVIOUR.get(t, t) for t in request.traps)
        )
    personas_text = "; ".join(f"{p.code} — {p.title}: {p.style}" for p in PERSONAS)

    if request.kind == "call_intake":
        task = (
            "Составь сценарий приёма вызова: заявитель звонит в 112, диспетчер-стажёр должен "
            "выяснить "
            "что случилось, адрес, пострадавших, имя и телефон заявителя. Нужны: title (коротко), "
            "difficulty, persona, noise, opening (первая фраза заявителя), facts (лист фактов: "
            "what_happened, address, injured, caller_name, callback_phone и другие важные детали, "
            "значения — строки), behaviour (как ведёт себя заявитель, что говорит сам и что только "
            "по вопросу), drops_call, replies — 15–25 реплик заявителя на все темы (topic из "
            "списка), "
            "включая сбивчивые варианты, «не знаю» (unknown) и «повторите» (repeat); "
            "required_topics — "
            "темы, которые диспетчер обязан выяснить; incident_type — код из списка кандидатов; "
            "flags — признаки карточки (injured и другие, true/false); address — адрес по полям "
            "(region только если это не Москва; descriptive — описательный адрес со слов "
            "заявителя); "
            "caller — имя, роль (очевидец, пострадавший, родственник…), телефон; description — "
            "описание со слов заявителя для карточки; description_keywords — 3–6 ключевых слов."
        )
    else:
        task = (
            "Составь сценарий «реагирование на карточку»: та же ситуация уже принята оператором "
            "112 "
            "и передана диспетчеру городской службы. Нужны: title, difficulty, incident_type — код "
            "из "
            "списка кандидатов, flags, address по полям, caller, description — текст описания в "
            "карточке (деловой стиль оператора 112), decision — accept, если служба должна принять "
            "карточку, reject — если адрес вне Москвы, дубль или не компетенция службы; "
            "reject_reason "
            "— код причины при reject; comments — примеры комментариев диспетчера: при accept три "
            "(начало реагирования с номером наряда, проведение работ, работы завершены), при "
            "reject один."
        )
    sample = examples.sample_for(request.kind, [str(r["code"]) for r in rows])
    sample_text = (
        "Образец готового сценария — так должны звучать реплики и так заполняется карточка. "
        f"Ситуация в нём другая, факты копировать нельзя:\n{sample}\n\n"
        if sample
        else ""
    )
    user = (
        f"{source}\n"
        f"Пожелания: {'; '.join(wishes) or 'нет'}.\n\n"
        f"Кандидаты типа происшествия (код: путь признаков):\n{_rows_text(rows)}\n\n"
        f"Темы реплик: {_topics_text()}.\n"
        f"Персонажи: {personas_text}.\n\n"
        f"Фрагменты памятки:\n{_chunks_text(ctx.memo_chunks)}\n\n"
        f"Похожие билеты:\n{_chunks_text(ctx.similar_tickets)}\n\n"
        f"{sample_text}"
        f"{task}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _address(generated: BaseModel) -> dict:
    return generated.model_dump(exclude_none=True)


def _card_address(generated: BaseModel, request: GenerationRequest) -> dict:
    """The address of the reference card comes from the ticket, not from the model. The card's
    hint list holds real Moscow streets and the trainee is graded against this card, so an
    address the model invented would make the task unfillable. The model's landmark description
    is kept when the ticket has none."""
    written = _address(generated)
    if request.facts is None:
        if request.address is None:
            return written
        # The real address, whatever the model wrote; its landmark description is kept.
        chosen = request.address.model_dump(exclude_none=True)
        if written.get("descriptive"):
            chosen["descriptive"] = written["descriptive"]
        return chosen
    parsed = request.facts.address.model_dump(exclude_none=True)
    if not parsed:
        return written
    descriptive = parsed.get("descriptive") or written.get("descriptive")
    if descriptive:
        parsed["descriptive"] = descriptive
    return parsed


def _card_caller(generated: BaseModel, request: GenerationRequest) -> dict:
    """Name, role and phone of the caller — also from the ticket when there is one."""
    if request.facts is None:
        return _address(generated)
    caller = request.facts.caller
    if not (caller.name or caller.phone):
        return _address(generated)
    return {
        "name": caller.name,
        "role": caller.relation or caller.role,
        "phone": caller.phone,
    }


def call_intake_body(gen: GeneratedCallIntake, row: Mapping, request: GenerationRequest) -> dict:
    flags = dict(gen.flags)
    flags.setdefault("injured", False)
    chosen = [k for k, v in flags.items() if v]
    return {
        "kind": "call_intake",
        "title": gen.title,
        "ticket_ref": request.ticket_ref,
        "difficulty": request.difficulty or gen.difficulty,
        "norm_seconds": template.DROPPED_CALL_NORM_SECONDS
        if gen.drops_call
        else template.DEFAULT_CALL_NORM_SECONDS,
        "caller": {
            "persona": request.persona or gen.persona,
            "voice": voice_for(
                request.persona or gen.persona,
                request.facts.caller.gender if request.facts else None,
            ),
            "noise": request.noise or gen.noise,
            "opening": gen.opening,
            "facts": gen.facts,
            "behaviour": gen.behaviour,
            "drops_call": gen.drops_call,
            "no_contact": False,
        },
        "replies": [
            {"id": i, "topic": r.topic, "text": r.text, "audio": None, "approved": False}
            for i, r in enumerate(gen.replies, start=1)
        ],
        "required_topics": list(dict.fromkeys(gen.required_topics)),
        "reference_card": {
            "signs_path": [row[k] for k in ("sign1", "sign2", "sign3") if row.get(k)],
            "incident_type": row["code"],
            "flags": flags,
            "address": _card_address(gen.address, request),
            "caller": _card_caller(gen.caller, request),
            "description_keywords": gen.description_keywords,
            "description": gen.description,
            "expected_services": resolve_services(row.get("service_rules") or [], chosen),
        },
        "traps": request.traps,
    }


def card_response_body(
    gen: GeneratedCardResponse, row: Mapping, request: GenerationRequest, ctx: GenerationContext
) -> dict:
    flags = dict(gen.flags)
    flags.setdefault("injured", False)
    chosen = [k for k, v in flags.items() if v]
    expected = resolve_services(row.get("service_rules") or [], chosen)
    # The trainee's own service (issue #265): whether it takes the card is the classifier's
    # answer, not the model's — a type it does not serve is the «не та служба» card.
    own = request.service in ctx.catalogue
    service = request.service if own else None
    if service is None:
        service = template.choose_card_service(expected, ctx.catalogue, row.get("main_service"))
    decision = ("accept" if service in expected else "reject") if own else gen.decision
    comments = list(gen.comments) + [""] * 3
    if own and decision == "reject":
        refused = template.card_reference(region=None, service=service, expected=expected)
        chain, critical = refused["status_chain"], refused["critical_errors"]
        reason = refused["reject_reason"]
    elif decision == "accept":
        chain = [
            {"status": "accepted"},
            {
                "status": "response_started",
                "order_number": True,
                "comment_example": comments[0] or "Направлен дежурный наряд",
            },
            {"status": "arrived"},
            {
                "status": "works_started",
                "comment_example": comments[1] or "Проводятся работы на месте",
            },
            {"status": "works_done", "comment_example": comments[2] or "Работы завершены"},
        ]
        critical = ["late_primary", "progress_missing"]
        reason = None
    else:
        chain = [{"status": "rejected", "comment_example": comments[0] or "Не принята"}]
        critical = ["empty_reject_comment"]
        reason = gen.reject_reason or "not_in_competence"
    notified = [{"service": s, "status": "Получена службой"} for s in expected if s != service]
    notified.append({"service": service, "status": "Добавлена"})
    facts = request.facts_or_phrase()
    return {
        "kind": "card_response",
        "title": gen.title,
        "ticket_ref": request.ticket_ref,
        "service": service,
        "difficulty": request.difficulty or gen.difficulty,
        "norm_seconds": template.CARD_NORM_SECONDS,
        "card": {
            "number": template._card_number(f"{request.ticket_ref}:{facts.situation}:{gen.title}"),
            "incident_type": row["code"],
            "signs": [row[k] for k in ("sign1", "sign2", "sign3") if row.get(k)],
            "flags": flags,
            "address": _card_address(gen.address, request),
            "caller": _card_caller(gen.caller, request),
            "description": gen.description,
            "notified": notified,
        },
        "reference": {
            "decision": decision,
            "reject_reason": reason,
            "status_chain": chain,
            "critical_errors": critical,
        },
        "traps": request.traps,
    }


class LlmGeneration:
    method = "llm"

    def __init__(self, chat: ChatModel, fallback: TemplateGeneration | None = None) -> None:
        self._chat = chat
        self._fallback = fallback or TemplateGeneration()

    async def available(self) -> bool:
        return await self._chat.available()

    async def generate(
        self, request: GenerationRequest, ctx: GenerationContext
    ) -> GenerationResult:
        started = time.perf_counter()
        request = with_real_address(request, ctx)
        rows = candidate_rows(request, ctx.type_rows)
        by_code = {str(r["code"]): r for r in rows}
        codes = list(by_code)
        schema = (
            call_intake_schema(codes)
            if request.kind == "call_intake"
            else card_response_schema(codes)
        )
        model_cls: type[BaseModel] = (
            GeneratedCallIntake if request.kind == "call_intake" else GeneratedCardResponse
        )
        messages = build_messages(request, ctx, rows)
        attempts = 0
        last_error: str | None = None
        raw: dict[str, Any] = {}
        while attempts <= JSON_RETRIES:
            attempts += 1
            try:
                raw = await self._chat.complete_json(
                    messages,
                    schema,
                    max_tokens=GENERATION_MAX_TOKENS,
                    temperature=GENERATION_TEMPERATURE,
                )
                generated = model_cls.model_validate(raw)
                if generated.incident_type not in by_code:  # type: ignore[attr-defined]
                    raise ModelOutputError(f"тип {generated.incident_type} не из списка кандидатов")  # type: ignore[attr-defined]
            except ModelUnavailableError as exc:
                result = await self._fallback.generate(request, ctx)
                result.note = f"Модель недоступна ({exc}); использован шаблон."
                result.attempts = attempts
                return result
            except (ModelOutputError, ValidationError) as exc:
                last_error = str(exc).splitlines()[0]
                log.warning("generation answer rejected", attempt=attempts, error=last_error)
                messages = [
                    *messages,
                    {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                    {
                        "role": "user",
                        "content": f"Ответ не прошёл проверку: {last_error}. "
                        "Исправь и верни JSON по схеме.",
                    },
                ]
                continue
            row = by_code[generated.incident_type]  # type: ignore[attr-defined]
            body = (
                call_intake_body(generated, row, request)  # type: ignore[arg-type]
                if request.kind == "call_intake"
                else plant_mistakes(
                    card_response_body(generated, row, request, ctx),  # type: ignore[arg-type]
                    ctx,
                    request,
                )
            )
            return GenerationResult(
                body=body,
                method=self.method,
                model=self._chat.name,
                attempts=attempts,
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        result = await self._fallback.generate(request, ctx)
        result.note = (
            f"Модель {attempts} раза вернула невалидный ответ ({last_error}); использован шаблон."
        )
        result.attempts = attempts
        return result


_provider: GenerationProvider | None = None


def build_generation_provider(
    llm_gen_url: str | None, api_key: str | None = None, api_model: str | None = None
) -> GenerationProvider:
    if not llm_gen_url:
        return TemplateGeneration()
    timeout = get_settings().llm_gen_timeout_seconds or GENERATION_TIMEOUT_SECONDS
    chat: ChatModel
    if api_key and api_model:  # both or neither: Settings refuses a key without a model
        chat = OpenAiCompatibleChat(llm_gen_url, api_key=api_key, model=api_model, timeout=timeout)
    else:
        chat = LlamaCppChat(llm_gen_url, name="llm-gen", timeout=timeout)
    return LlmGeneration(chat)


def get_generation_provider() -> GenerationProvider:
    global _provider
    if _provider is None:
        settings = get_settings()
        _provider = build_generation_provider(
            settings.llm_gen_url, settings.llm_gen_api_key, settings.llm_gen_api_model
        )
    return _provider
