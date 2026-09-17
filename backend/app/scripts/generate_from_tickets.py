"""Drafts of both modes for the organizers' tickets (PRD 9.6 item 1).

    python -m app.scripts.generate_from_tickets --all
    python -m app.scripts.generate_from_tickets --ticket 2-1 --kind call_intake
    python -m app.scripts.generate_from_tickets --all --provider template --force

Every ticket situation becomes a ``call_intake`` and a ``card_response`` scenario with
``source=organizers`` and status ``review``; the teacher approves them in the library. Existing
drafts of a ticket are kept unless ``--force`` (then a new version is added). ``--provider``
picks the generator: ``auto`` uses the model when ``LLM_GEN_URL`` answers, else the template.
The model takes a minute or two per scenario on a CPU, so ``--all`` with the model is a job for
the night; the template finishes the 96 situations in seconds.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from sqlalchemy import select

from app.db import SessionLocal
from app.domain.scenarios.facts import parse_ticket
from app.domain.scenarios.validate import fill_from_reference
from app.logging import configure_logging
from app.models import SCENARIO_REVIEW, Scenario, Ticket
from app.providers.generation import (
    GenerationContext,
    GenerationProvider,
    GenerationRequest,
    TemplateGeneration,
    get_generation_provider,
)
from app.providers.rag import get_reference_index
from app.scenarios import service as scenarios

KINDS = ("call_intake", "card_response")
SOURCE = "organizers"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", help="все ситуации из билетов")
    target.add_argument("--ticket", help="одна ситуация, например 2-1")
    parser.add_argument("--kind", choices=KINDS, help="только один режим")
    parser.add_argument(
        "--provider",
        choices=("auto", "template"),
        default="auto",
        help="генератор (по умолчанию auto)",
    )
    parser.add_argument("--force", action="store_true", help="добавить новую версию существующим")
    parser.add_argument("--limit", type=int, default=0, help="не больше N ситуаций (для проб)")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    provider: GenerationProvider = (
        TemplateGeneration() if args.provider == "template" else get_generation_provider()
    )
    if provider.method == "llm" and not await provider.available():
        print("Модель llm-gen не отвечает: генерируем шаблоном.", file=sys.stderr)
        provider = TemplateGeneration()
    kinds = [args.kind] if args.kind else list(KINDS)
    created = updated = skipped = 0
    started = time.perf_counter()

    async with SessionLocal() as session:
        refs = await scenarios.load_refs(session)
        catalogue = await scenarios.load_catalogue(session)
        query = select(Ticket).order_by(Ticket.ticket_no, Ticket.item_no)
        if args.ticket:
            no, item = args.ticket.split("-", 1)
            query = query.where(Ticket.ticket_no == int(no), Ticket.item_no == int(item))
        tickets = list((await session.scalars(query)).all())
        if args.limit:
            tickets = tickets[: args.limit]
        if not tickets:
            print("Билеты не найдены: сначала импорт организаторов.", file=sys.stderr)
            return 1
        index = get_reference_index()
        index.set_tickets(
            [(f"{t.ticket_no}-{t.item_no}", f"{t.situation} — {t.address}") for t in tickets]
        )
        type_rows = list(refs.incident_types.values())

        for position, ticket in enumerate(tickets, start=1):
            ref = f"{ticket.ticket_no}-{ticket.item_no}"
            facts = parse_ticket(ticket.situation, ticket.address)
            with_model = provider.method == "llm"
            ctx = GenerationContext(
                type_rows=type_rows,
                catalogue=catalogue,
                memo_chunks=index.search_memo(facts.what_happened) if with_model else (),
                similar_tickets=index.similar_tickets(facts.what_happened) if with_model else (),
            )
            for kind in kinds:
                existing = await session.scalar(
                    select(Scenario).where(
                        Scenario.ticket_ref == ref, Scenario.kind == kind, Scenario.source == SOURCE
                    )
                )
                if existing is not None and not args.force:
                    skipped += 1
                    continue
                request = GenerationRequest(
                    kind=kind, facts=facts, ticket_ref=ref, traps=list(ticket.traps or [])
                )
                result = await provider.generate(request, ctx)
                meta = scenarios._generation_meta(result, None)
                if existing is None:
                    await scenarios.create(
                        session,
                        result.body,
                        source=SOURCE,
                        status=SCENARIO_REVIEW,
                        author=None,
                        refs=refs,
                        generation=meta,
                        revision_comment=f"Черновик из билета {ref}",
                    )
                    created += 1
                else:
                    loaded = await scenarios.load(session, existing.id, for_write=True)
                    body = fill_from_reference(result.body, refs)
                    body["generation"] = meta
                    body = scenarios._carry_approved(loaded.version.body, body)
                    await scenarios.add_version(
                        session, loaded, body, comment="Перегенерировано из билета", author=None
                    )
                    updated += 1
            print(f"[{position}/{len(tickets)}] {ref}: {facts.what_happened[:60]}", flush=True)
        await session.commit()

    seconds = time.perf_counter() - started
    print(
        f"Готово за {seconds:.1f} с: создано {created}, обновлено {updated}, "
        f"пропущено {skipped} (уже есть), генератор: {provider.method}."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging("WARNING")
    return asyncio.run(run(parse_args(argv if argv is not None else sys.argv[1:])))


if __name__ == "__main__":
    sys.exit(main())
