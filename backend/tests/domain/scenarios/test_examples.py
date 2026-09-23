"""A delivered scenario as the sample in the generation prompt (issue: quality of generation)."""

from __future__ import annotations

import json
import random

from app.domain.scenarios import examples


def test_sample_comes_from_the_branch_of_the_candidates() -> None:
    """A fire is shown a fire, a scuffle a scuffle: the first level of the code must match."""
    rng = random.Random(0)
    for codes, branch in [(["1.2.2.1"], "1"), (["15.6.2.2"], "15"), (["22.8.0.0"], "22")]:
        body = examples.pick("call_intake", codes, rng=rng)
        assert body is not None
        assert examples._branch(body["reference_card"]["incident_type"]) == branch


def test_sample_is_trimmed_but_keeps_what_the_model_must_copy() -> None:
    text = examples.sample_for("call_intake", ["1.2.2.1"], rng=random.Random(1))
    assert text is not None
    sample = json.loads(text)
    assert sample["caller"]["opening"] and sample["caller"]["behaviour"]
    assert 1 <= len(sample["replies"]) <= examples.REPLIES_IN_SAMPLE
    assert len({r["topic"] for r in sample["replies"]}) == len(sample["replies"])  # без повторов тем
    card = sample["reference_card"]
    assert card["incident_type"] and card["description"]
    # Признаки и службы движок считает сам по классификатору — модели их видеть незачем.
    assert "signs_path" not in card and "expected_services" not in card


def test_sample_of_a_card_scenario_shows_the_comments() -> None:
    text = examples.sample_for("card_response", ["13.2.3.0"], rng=random.Random(2))
    if text is None:  # поставка без карточных сценариев
        return
    sample = json.loads(text)
    assert "replies" not in sample
    assert sample["card"]["incident_type"]
    assert sample["reference"]["decision"] in ("accept", "reject")


def test_without_delivered_scenarios_there_is_no_sample(monkeypatch) -> None:
    monkeypatch.setattr(examples, "_delivered", lambda: ())
    assert examples.sample_for("call_intake", ["1.2.2.1"]) is None
