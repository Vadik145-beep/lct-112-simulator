"""Generates the virtual cohort the readiness forecast is trained on (PRD 9.7): 300 trainees
with a hidden level, a noisy history of attempts and a «certification» on 20 held-out attempts.

Run from the repository root: python scripts/simulate_cohort.py [--size 300] [--seed N]
Writes ai/data/cohort.json; then: cd backend && python -m app.domain.analytics.train_readiness
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.analytics.cohort import save_cohort  # noqa: E402
from app.domain.analytics.simulate import simulate_cohort  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument("--out", type=Path, default=ROOT / "ai" / "data")
    args = parser.parse_args()
    students = simulate_cohort(args.size, seed=args.seed)
    path = save_cohort(students, args.out, seed=args.seed)
    ready = sum(1 for s in students if s.ready)
    attempts = sum(len(s.history) for s in students)
    print(
        f"обучающихся: {len(students)}, попыток в истории: {attempts}, "
        f"прошли аттестацию: {ready} ({100 * ready / len(students):.0f} %)"
    )
    print(f"записано: {path}")


if __name__ == "__main__":
    main()
