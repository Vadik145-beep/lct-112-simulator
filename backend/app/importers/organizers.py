"""Loads the organizers' dataset into the reference tables. Idempotent.

    docker compose exec backend python -m app.importers.organizers

Sources (DATA_DIR, see app.config):

* ``organizers/Классификатор_происшествий_v046.xlsx`` — when present it is parsed and the
  result is written to ``seed/classifier.json``; the stand has no organizers' files and
  loads the committed ``seed/classifier.json`` instead;
* ``seed/tickets.json`` — 96 situations recognized from the ticket scans
  (``app.importers.tickets_ocr``);
* ``seed/streets.json`` — Moscow streets with okrug and district (``app.importers.streets_osm``);
* statuses, card statuses, reject reasons, typical errors and caller topics come from
  ``app.domain.reference_data`` (quoted from the memo).

Every table is upserted by code; rows that disappeared from the source are deleted, so a
re-run after a classifier update leaves the database equal to the source.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.domain import reference_data
from app.importers.classifier import FLAGS, Classifier, parse_classifier, service_catalog
from app.logging import configure_logging, get_logger
from app.models import (
    CallerTopic,
    CardStatus,
    IncidentFlag,
    IncidentGroup,
    IncidentType,
    RejectReason,
    ResponseStatus,
    Service,
    Street,
    Ticket,
    TypicalError,
)

log = get_logger(__name__)

CLASSIFIER_XLSX = "Классификатор_происшествий_v046.xlsx"
CLASSIFIER_JSON = "classifier.json"
TICKETS_JSON = "tickets.json"
STREETS_JSON = "streets.json"
# Streets from the tickets that the OSM extract lacks; added by hand with source «manual».
STREETS_MANUAL_JSON = "streets_manual.json"

# Heuristic ticket traps (PRD 8.3): what the situation tests besides the standard flow.
TRAP_PATTERNS: dict[str, re.Pattern[str]] = {
    "other_region": re.compile(
        r"\b(обл\.|область|московская обл|МО,|г\. королёв|рязан|тульск|владимирск|"
        r"волгоградск|химки|балашиха|красногорск|домодед|люберец|одинцов|дмитровск|"
        r"раменск|ржев|клин|зеленоград)",
        re.IGNORECASE,
    ),
    "descriptive_address": re.compile(
        r"(около|напротив|рядом|между|не доезжая|дорога от|съезд|в сторону|переход со|"
        r"вход от|на пересечении|№ дома неизвестен|км\b)",
        re.IGNORECASE,
    ),
    "call_dropped": re.compile(r"бросил трубку", re.IGNORECASE),
    "injured": re.compile(
        r"(пострадавш(?!их нет|их не видят|их людей нет)|травм|кровотеч|без сознания|ожог|"
        r"потеря сознания|плохо\b)",
        re.IGNORECASE,
    ),
    "child": re.compile(r"(ребен|ребён|подросток|\b\d{1,2} лет\b.*(упал|задыха))", re.IGNORECASE),
    "caller_not_victim": re.compile(
        r"(вызывает (мама|папа|супруг|муж|жена|брат|дочь|сын|отец|подруга|прохожий|кассир|"
        r"администратор)|очевидец|прохожий|сосед)",
        re.IGNORECASE,
    ),
    "no_ambulance": re.compile(r"03 не (треб|требуется)", re.IGNORECASE),
    "entrance_details": re.compile(r"(под\.|подъезд|эт\.|этаж|код\b|домофон)", re.IGNORECASE),
}


def normalize_street(name: str) -> str:
    return re.sub(r"\s+", " ", name.lower().replace("ё", "е")).strip()


def detect_traps(situation: str, address: str) -> list[str]:
    text = f"{situation} {address}"
    traps = [code for code, pattern in TRAP_PATTERNS.items() if pattern.search(text)]
    if "other_region" in traps and re.search(r"(^|\W)Москва(?!\s*обл)", address):
        # «Москва, …» beats a mention of a Moscow-region town inside the description.
        traps.remove("other_region")
    return traps


def classifier_to_json(classifier: Classifier) -> dict:
    return {
        "groups": [asdict(g) for g in classifier.groups],
        "types": [
            {**asdict(t), "service_rules": [r.as_dict() for r in t.service_rules]}
            for t in classifier.types
        ],
        "services": service_catalog(classifier.service_codes),
        "flags": [
            {"code": code, "title": title, "column_hint": hint, "order": i}
            for i, (code, (title, hint)) in enumerate(FLAGS.items(), start=1)
        ],
        "unparsed_columns": classifier.unparsed_columns,
        "warnings": classifier.warnings,
    }


def load_classifier(data_dir: Path) -> tuple[dict, str]:
    """Parses the xlsx when available (and refreshes seed/classifier.json), else loads JSON."""
    xlsx = data_dir / "organizers" / CLASSIFIER_XLSX
    seed = data_dir / "seed" / CLASSIFIER_JSON
    if xlsx.exists():
        payload = classifier_to_json(parse_classifier(xlsx))
        text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
        if seed.exists() and seed.read_text(encoding="utf-8") == text:
            return payload, f"xlsx ({xlsx.name}), {seed.name} без изменений"
        try:
            seed.parent.mkdir(parents=True, exist_ok=True)
            seed.write_text(text, "utf-8")
        except OSError as exc:  # /data is mounted read-only in the containers
            log.warning("seed not refreshed", path=str(seed), error=str(exc))
            return payload, f"xlsx ({xlsx.name}), {seed.name} не обновлён: папка только для чтения"
        return payload, f"xlsx ({xlsx.name}), обновлён {seed.name}"
    if seed.exists():
        return json.loads(seed.read_text(encoding="utf-8")), f"seed/{CLASSIFIER_JSON}"
    raise FileNotFoundError(
        f"Нет ни {xlsx} ни {seed}: положите файлы организаторов в data/organizers/ "
        "или возьмите data/seed/classifier.json из репозитория."
    )


async def upsert(session: AsyncSession, model, rows: list[dict], key: str = "code") -> None:
    """Inserts or updates rows by key and deletes rows missing from the source."""
    if not rows:
        return
    columns = [c.name for c in model.__table__.columns if c.name != key]
    statement = insert(model).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[key],
        set_={c: getattr(statement.excluded, c) for c in columns if c in rows[0]},
    )
    await session.execute(statement)
    keys = [row[key] for row in rows]
    await session.execute(delete(model).where(getattr(model, key).not_in(keys)))


async def import_classifier(session: AsyncSession, payload: dict) -> dict[str, int]:
    services = [{**s, "order": i} for i, s in enumerate(payload["services"], start=1)]
    await upsert(session, Service, services)
    await upsert(session, IncidentFlag, payload["flags"])
    await upsert(session, IncidentGroup, payload["groups"])
    types = [
        {k: v for k, v in t.items() if k in IncidentType.__table__.columns}
        for t in payload["types"]
    ]
    for t in types:
        t.setdefault("required_topics", [])
    # Types of vanished groups go away with the group (ON DELETE CASCADE), so groups first
    # and types in chunks to keep statements small.
    for start in range(0, len(types), 200):
        chunk = types[start : start + 200]
        statement = insert(IncidentType).values(chunk)
        columns = [c.name for c in IncidentType.__table__.columns if c.name != "code"]
        statement = statement.on_conflict_do_update(
            index_elements=["code"], set_={c: getattr(statement.excluded, c) for c in columns}
        )
        await session.execute(statement)
    await session.execute(
        delete(IncidentType).where(IncidentType.code.not_in([t["code"] for t in types]))
    )
    return {
        "services": len(services),
        "flags": len(payload["flags"]),
        "groups": len(payload["groups"]),
        "types": len(types),
    }


async def import_memo_reference(session: AsyncSession) -> dict[str, int]:
    await upsert(session, ResponseStatus, reference_data.RESPONSE_STATUSES)
    await upsert(session, CardStatus, reference_data.CARD_STATUSES)
    await upsert(session, RejectReason, reference_data.REJECT_REASONS)
    await upsert(session, TypicalError, reference_data.TYPICAL_ERRORS)
    await upsert(session, CallerTopic, reference_data.CALLER_TOPICS)
    return {
        "response_statuses": len(reference_data.RESPONSE_STATUSES),
        "card_statuses": len(reference_data.CARD_STATUSES),
        "reject_reasons": len(reference_data.REJECT_REASONS),
        "typical_errors": len(reference_data.TYPICAL_ERRORS),
        "caller_topics": len(reference_data.CALLER_TOPICS),
    }


async def import_tickets(session: AsyncSession, data_dir: Path) -> int:
    path = data_dir / "seed" / TICKETS_JSON
    if not path.exists():
        log.warning("tickets seed missing", path=str(path))
        return 0
    items = json.loads(path.read_text(encoding="utf-8"))
    existing = {
        (t.ticket_no, t.item_no): t.id for t in (await session.scalars(select(Ticket))).all()
    }
    rows = []
    for item in items:
        key = (item["ticket_no"], item["item_no"])
        row = {
            "ticket_no": item["ticket_no"],
            "item_no": item["item_no"],
            "situation": item["situation"],
            "address": item["address"],
            "ocr_confident": item["ocr_confident"],
            "ocr_confidence": item.get("confidence"),
            "traps": detect_traps(item["situation"], item["address"]),
        }
        if key in existing:
            row["id"] = existing[key]
        else:
            row["id"] = uuid.uuid4()
        rows.append(row)
    statement = insert(Ticket).values(rows)
    columns = ["situation", "address", "ocr_confident", "ocr_confidence", "traps"]
    statement = statement.on_conflict_do_update(
        constraint="uq_tickets_ticket_item",
        set_={c: getattr(statement.excluded, c) for c in columns},
    )
    await session.execute(statement)
    await session.execute(delete(Ticket).where(Ticket.id.not_in([r["id"] for r in rows])))
    return len(rows)


async def import_streets(session: AsyncSession, data_dir: Path) -> int:
    path = data_dir / "seed" / STREETS_JSON
    if not path.exists():
        log.warning("streets seed missing", path=str(path))
        return 0
    items = json.loads(path.read_text(encoding="utf-8"))
    manual = data_dir / "seed" / STREETS_MANUAL_JSON
    if manual.exists():
        items += [
            {**row, "source": "manual"} for row in json.loads(manual.read_text(encoding="utf-8"))
        ]
    seen: set[tuple[str, str, str]] = set()
    rows = []
    for item in items:
        key = (item["name"], item["okrug"], item["district"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "id": uuid.uuid4(),
                "name": item["name"],
                "name_normalized": normalize_street(item["name"]),
                "okrug": item["okrug"],
                "district": item["district"],
                "source": item.get("source", "osm"),
            }
        )
    # Streets are only read by the API, so replacing the table wholesale is the simplest
    # way to stay equal to the seed file.
    await session.execute(delete(Street))
    for start in range(0, len(rows), 1000):
        await session.execute(insert(Street).values(rows[start : start + 1000]))
    return len(rows)


async def run(data_dir: Path | None = None) -> dict[str, int]:
    data_dir = data_dir or Path(get_settings().data_dir)
    payload, source = load_classifier(data_dir)
    async with SessionLocal() as session:
        summary = await import_classifier(session, payload)
        summary.update(await import_memo_reference(session))
        summary["tickets"] = await import_tickets(session, data_dir)
        summary["streets"] = await import_streets(session, data_dir)
        await session.commit()
        summary["types_in_db"] = await session.scalar(select(func.count(IncidentType.code)))
    summary["classifier_source"] = source  # type: ignore[assignment]
    summary["unparsed_columns"] = len(payload.get("unparsed_columns", []))
    summary["warnings"] = len(payload.get("warnings", []))
    return summary


def print_summary(summary: dict) -> None:
    print(f"Классификатор: {summary['classifier_source']}")
    print(
        f"  групп {summary['groups']}, итоговых типов {summary['types']}, "
        f"признаков {summary['flags']}, служб {summary['services']}, "
        f"неразобранных колонок {summary['unparsed_columns']}, предупреждений {summary['warnings']}"
    )
    print(
        f"Памятка: статусов реагирования {summary['response_statuses']}, "
        f"статусов карточки {summary['card_statuses']}, причин отказа {summary['reject_reasons']}, "
        f"типичных ошибок {summary['typical_errors']}, тем заявителя {summary['caller_topics']}"
    )
    print(f"Билеты: ситуаций {summary['tickets']}")
    print(f"Улицы: {summary['streets']}")
    print(f"В базе итоговых типов: {summary['types_in_db']}")


def main() -> int:
    configure_logging(get_settings().log_level)
    try:
        summary = asyncio.run(run())
    except FileNotFoundError as exc:
        print(exc)
        return 1
    print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
