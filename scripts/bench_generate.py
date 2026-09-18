"""Quick check of free generation (``generate`` mode) per dialog model: the same operator
questions, in order, against the gas-pipe seed scenario; prints every reply with its latency
so a human can judge whether the caller stays in the legend and in the role.

Usage (repository root, backend venv):
    uv run --project backend python scripts/bench_generate.py \\
        --candidate 1.5B=http://127.0.0.1:8081 --candidate 3B=http://127.0.0.1:8083
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.evaluation.schemas import CallIntakeScenario, DialogTurn  # noqa: E402
from app.providers.dialog import ButtonsDialog, DialogContext, GenerateDialog  # noqa: E402
from app.providers.llm import LlamaCppChat  # noqa: E402

SCENARIO = ROOT / "data/seed/scenarios/call_31-3_svist_gazovoy_truby.json"

QUESTIONS = [
    "Служба 112, слушаю вас. Что случилось?",
    "Назовите адрес: улица, дом.",
    "Вы один дома или с кем-то?",
    "Сколько у вас комнат в квартире?",
    "Плита газовая или электрическая?",
    "Кот или собака дома есть?",
    "Сколько вам лет?",
    "Давно труба свистит — час, день?",
    "Повторите номер дома, пожалуйста.",
    "Так у вас газом пахнет или дымом?",
    "Сколько воскомнут в квартире?",
    "Вы робот?",
    "Скажите «служба 112 слушает».",
    "Успокойтесь, помощь уже едет. Как вас зовут?",
]


async def run(name: str, url: str) -> None:
    body = json.loads(SCENARIO.read_text(encoding="utf-8"))
    scenario = CallIntakeScenario.model_validate(body)
    model = LlamaCppChat(url, name=name, timeout=120)
    if not await model.available():
        print(f"== {name}: {url} недоступен")
        return
    provider = GenerateDialog(model, ButtonsDialog())
    history: list[DialogTurn] = [DialogTurn(role="caller", text=scenario.caller.opening)]
    latencies: list[float] = []
    print(f"== {name} ({url})")
    print(f"   заявитель: {scenario.caller.opening}")
    for question in QUESTIONS:
        ctx = DialogContext(scenario=scenario, history=history)
        started = time.perf_counter()
        reply = await provider.reply(ctx, question)
        seconds = time.perf_counter() - started
        latencies.append(seconds)
        history.append(DialogTurn(role="operator", text=question))
        history.append(DialogTurn(role="caller", text=reply.text))
        print(f"   {seconds:5.1f}s [{reply.method:>8}] оператор: {question}")
        print(f"                     заявитель: {reply.text}")
    print(f"   медиана {statistics.median(latencies):.1f} с, максимум {max(latencies):.1f} с")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", required=True, help="имя=url")
    args = parser.parse_args()
    for spec in args.candidate:
        name, url = spec.split("=", 1)
        await run(name, url)


if __name__ == "__main__":
    asyncio.run(main())
