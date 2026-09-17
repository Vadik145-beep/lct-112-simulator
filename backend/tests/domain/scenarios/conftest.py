"""Shared data for the scenario domain tests: the seed classifier and tickets as dictionaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.scenarios.template import ServiceInfo

DATA_DIR = Path(__file__).resolve().parents[4] / "data"
CLASSIFIER = DATA_DIR / "seed" / "classifier.json"
TICKETS = DATA_DIR / "seed" / "tickets.json"

requires_seed = pytest.mark.skipif(
    not (CLASSIFIER.exists() and TICKETS.exists()), reason="нет data/seed/classifier.json"
)


@pytest.fixture(scope="module")
def classifier() -> dict:
    return json.loads(CLASSIFIER.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def type_rows(classifier: dict) -> list[dict]:
    return classifier["types"]


@pytest.fixture(scope="module")
def catalogue(classifier: dict) -> dict[str, ServiceInfo]:
    return {
        s["code"]: ServiceInfo(s["code"], bool(s.get("via_arm112", True)))
        for s in classifier["services"]
    }


@pytest.fixture(scope="module")
def tickets() -> list[dict]:
    return json.loads(TICKETS.read_text(encoding="utf-8"))


def ticket(tickets: list[dict], ref: str) -> dict:
    no, item = ref.split("-")
    return next(t for t in tickets if t["ticket_no"] == int(no) and t["item_no"] == int(item))
