"""A hand-written scenario shown to the model as the sample of the wording we expect.

The 96 delivered scenarios (``data/seed/scenarios``) are written by hand against the customer's
tickets and checked, so they are the best description of «a good scenario» we have — better than
any wording of the rules in the prompt. One of them goes into the generation prompt: the same
kind, and by preference from the same branch of the classifier as the candidates of this request
(a fire reads unlike a scuffle). The sample is trimmed — the opening, the behaviour, a few
replies of different topics and the reference card — so a small model keeps it in its window.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

SCENARIOS_DIR = Path(__file__).resolve().parents[4] / "data" / "seed" / "scenarios"
REPLIES_IN_SAMPLE = 8


@lru_cache(maxsize=1)
def _delivered() -> tuple[dict[str, Any], ...]:
    if not SCENARIOS_DIR.is_dir():
        return ()
    bodies = []
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if body.get("replies") or body.get("reference"):
            bodies.append(body)
    return tuple(bodies)


def _branch(code: str | None) -> str:
    """Top level of the classifier code: «1.2.2.1» → «1» (fires, DTP, crimes…)."""
    return (code or "").split(".", 1)[0]


def _card(body: dict[str, Any]) -> dict[str, Any]:
    """The incident card of the scenario: the reference one a trainee fills in a call, the
    incoming one a service dispatcher works with."""
    return body.get("reference_card") or body.get("card") or {}


def pick(kind: str, codes: Sequence[str], *, rng: random.Random | None = None) -> dict[str, Any] | None:
    """A delivered scenario of this kind, preferring the branch of the candidate codes."""
    same_kind = [b for b in _delivered() if b.get("kind") == kind]
    if not same_kind:
        return None
    branches = {_branch(c) for c in codes if c}
    near = [b for b in same_kind if _branch(_card(b).get("incident_type")) in branches]
    chooser = rng or random
    return chooser.choice(near or same_kind)


def _replies_sample(body: dict[str, Any]) -> list[dict[str, str]]:
    """Up to ``REPLIES_IN_SAMPLE`` replies, one per topic, in the order of the scenario."""
    seen: set[str] = set()
    sample = []
    for reply in body.get("replies") or []:
        topic = reply.get("topic")
        if topic in seen:
            continue
        seen.add(topic)
        sample.append({"topic": topic, "text": reply.get("text", "")})
        if len(sample) == REPLIES_IN_SAMPLE:
            break
    return sample


def as_prompt_text(body: dict[str, Any]) -> str:
    caller = body.get("caller") or {}
    card = {k: v for k, v in _card(body).items() if k not in ("signs_path", "expected_services")}
    sample = {
        "title": body.get("title"),
        "difficulty": body.get("difficulty"),
        "caller": {
            "persona": caller.get("persona"),
            "noise": caller.get("noise"),
            "opening": caller.get("opening"),
            "facts": caller.get("facts"),
            "behaviour": caller.get("behaviour"),
        },
        "replies": _replies_sample(body),
        "reference_card": card,
    }
    if body.get("kind") != "call_intake":
        sample.pop("caller", None)
        sample.pop("replies", None)
        sample.pop("reference_card", None)
        sample["service"] = body.get("service")
        sample["card"] = card
        sample["reference"] = body.get("reference") or {}
    return json.dumps(sample, ensure_ascii=False, indent=2)


def sample_for(kind: str, codes: Sequence[str], *, rng: random.Random | None = None) -> str | None:
    body = pick(kind, codes, rng=rng)
    return as_prompt_text(body) if body else None
