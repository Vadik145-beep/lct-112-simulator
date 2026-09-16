"""Section «Показать» of wave 5: a question in text → the reply number → an MP3 to listen to.

Runs without the database: the scenario comes from ``data/seed/scenarios``, the caller answers
through the dialog model at ``--llm`` (or by keywords when it is not up), the reply is voiced
with Piper from ``--models-dir``. Files land in ``--out`` (default docs/screenshots/wave-05/demo).

    uv run --project backend python scripts/demo_dialog.py \\
        --scenario call_2-1_zadymlenie_musoroprovoda --llm http://localhost:8081 \\
        "Что случилось?" "Диктуйте адрес" "На каком этаже?" "Кто-нибудь пострадал?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.evaluation.schemas import CallIntakeScenario, DialogTurn  # noqa: E402
from app.providers.dialog import DialogContext, build_dialog_provider  # noqa: E402
from app.providers.llm import LlamaCppChat  # noqa: E402
from app.providers.tts import PiperTTS, save_clip  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("questions", nargs="+", help="фразы оператора по порядку")
    parser.add_argument("--scenario", default="call_2-1_zadymlenie_musoroprovoda")
    parser.add_argument("--llm", default="http://localhost:8081", help="сервер llama.cpp диалога")
    parser.add_argument(
        "--mode", default="select", choices=["select", "hybrid", "generate", "buttons"]
    )
    parser.add_argument("--models-dir", default=str(ROOT / "models"))
    parser.add_argument("--out", default=str(ROOT / "docs" / "screenshots" / "wave-05" / "demo"))
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    body = json.loads(
        (ROOT / "data" / "seed" / "scenarios" / f"{args.scenario}.json").read_text("utf-8")
    )
    scenario = CallIntakeScenario.model_validate(body)
    model = LlamaCppChat(args.llm, name="llm-dialog") if args.mode != "buttons" else None
    if model is not None and not await model.available():
        print(f"сервер {args.llm} не отвечает: заявитель отвечает по ключевым словам (buttons)")
        model = None
    provider = build_dialog_provider(args.mode, model)
    tts = PiperTTS(Path(args.models_dir) / "tts")
    out = Path(args.out)

    ctx = DialogContext(scenario=scenario, conversation_id="demo")
    ctx.history.append(DialogTurn(role="caller", text=scenario.caller.opening))
    print(
        f"Сценарий: {scenario.title} (голос {scenario.caller.voice}, шум {scenario.caller.noise})"
    )
    print(f"Заявитель: {scenario.caller.opening}\n")
    for n, question in enumerate(args.questions, 1):
        started = time.perf_counter()
        reply = await provider.reply(ctx, question)
        model_ms = round((time.perf_counter() - started) * 1000)
        clip = await tts.synthesize(
            reply.text,
            scenario.caller.voice,
            scenario.caller.noise,
            scenario.difficulty,
        )
        tts_ms = round((time.perf_counter() - started) * 1000) - model_ms
        paths = save_clip(clip, out / f"{n:02d}_r{reply.reply_id or 'gen'}") if clip else {}
        audio = paths.get("mp3") or paths.get("wav")
        ctx.history.append(DialogTurn(role="operator", text=question, topics=reply.operator_topics))
        ctx.history.append(DialogTurn(role="caller", text=reply.text, topics=reply.topics))
        if reply.reply_id:
            ctx.used_reply_ids.add(reply.reply_id)
        print(f"Оператор:  {question}")
        print(f"Заявитель: {reply.text}")
        print(
            f"           реплика №{reply.reply_id} [{reply.method}] темы {reply.topics}; "
            f"модель {model_ms} мс, озвучка {tts_ms} мс → {audio}\n"
        )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    Path(args.out).mkdir(parents=True, exist_ok=True)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
