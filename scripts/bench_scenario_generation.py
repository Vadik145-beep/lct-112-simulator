"""Scenario generation per model: the same «generate for the lesson» requests
(``scenarios.plan_for_groups``) against each llama.cpp server, printing method (model or the
template fallback), latency, title, type and the number of replies, so a human can judge
whether a smaller model than the planned 7B is usable for drafting scenarios.

Needs the reference tables in the database the environment points to (``DATABASE_URL`` or
``TEST_DATABASE_URL``) — the memo and tickets index (e5 on CPU) is built on first use.

Usage (repository root, backend venv):
    uv run --project backend python scripts/bench_scenario_generation.py \\
        --candidate 3B=http://127.0.0.1:8081 --candidate 1.5B=http://127.0.0.1:8181 \\
        --groups 24,1,2,13,17 --kind call_intake --seed 7
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

if "DATABASE_URL" not in os.environ and "TEST_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ.setdefault("DATABASE_ADMIN_URL", os.environ.get("TEST_DATABASE_ADMIN_URL", ""))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import IncidentGroup  # noqa: E402
from app.providers.generation import (  # noqa: E402
    LlmGeneration,
    TemplateGeneration,
)
from app.providers.llm import LlamaCppChat  # noqa: E402
from app.scenarios import service as scenarios  # noqa: E402


async def run(name: str, url: str | None, args: argparse.Namespace) -> None:
    if url:
        chat = LlamaCppChat(url, name=name, timeout=args.timeout)
        if not await chat.available():
            print(f"== {name}: {url} недоступен")
            return
        provider = LlmGeneration(chat)
    else:
        provider = TemplateGeneration()
    async with SessionLocal() as session:
        refs = await scenarios.load_refs(session)
        titles = {
            g.code: g.title
            for g in await session.scalars(select(IncidentGroup).order_by(IncidentGroup.number))
        }
        groups = args.groups.split(",") if args.groups else list(titles)[: args.count]
        requests = scenarios.plan_for_groups(
            refs,
            kind=args.kind,
            groups=groups,
            titles=titles,
            difficulty=args.difficulty,
            service_profile=[],
            count=args.count,
            rng=random.Random(args.seed),  # noqa: S311
        )
        print(f"== {name} ({url or 'шаблон'}), {len(requests)} запросов, режим {args.kind}")
        latencies: list[float] = []
        by_model = 0
        for request in requests:
            ctx = await scenarios._generation_context(session, refs, request.phrase or "")
            started = time.perf_counter()
            result = await provider.generate(request, ctx)
            seconds = time.perf_counter() - started
            latencies.append(seconds)
            by_model += result.method == "llm"
            body = result.body
            replies = body.get("replies") or []
            card = body.get("reference_card") or body.get("card") or {}
            print(
                f"   {seconds:6.1f}s [{result.method:>8}] попыток {result.attempts} "
                f"группа {request.incident_group} → тип {card.get('incident_type')} "
                f"«{body.get('title')}», реплик {len(replies)}"
            )
            if result.note:
                print(f"           {result.note}")
            if args.verbose:
                print(json.dumps(body, ensure_ascii=False, indent=1)[: args.verbose])
        if latencies:
            median = statistics.median(latencies)
            print(
                f"   моделью {by_model}/{len(requests)}, медиана {median:.0f} с, "
                f"максимум {max(latencies):.0f} с"
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate", action="append", required=True, help="имя=url; url пустой = шаблон"
    )
    parser.add_argument(
        "--groups", default="", help="коды групп через запятую (по умолчанию первые --count)"
    )
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--kind", default="call_intake", choices=["call_intake", "card_response"])
    parser.add_argument("--difficulty", type=int, default=1)
    parser.add_argument("--seed", type=int, default=7, help="выбор типа в группе воспроизводим")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--verbose", type=int, default=0, help="печатать первые N символов тела")
    args = parser.parse_args()
    for spec in args.candidate:
        name, _, url = spec.partition("=")
        await run(name, url or None, args)


if __name__ == "__main__":
    asyncio.run(main())
