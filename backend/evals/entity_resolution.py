from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CASES_PATH = Path(__file__).parent / "entity_resolution_cases.yaml"
_REQUIRED = ("id", "question", "expected_tickers")


@dataclass(frozen=True)
class EntityResolutionCase:
    id: str
    question: str
    expected_tickers: list[str]


def load_cases(path: Path = CASES_PATH) -> list[EntityResolutionCase]:
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    cases = []
    for entry in entries:
        entry_id = entry.get("id", "<missing id>")
        for field in _REQUIRED:
            if field not in entry:
                raise ValueError(f"case {entry_id}: missing field {field!r}")
        if not isinstance(entry["expected_tickers"], list):
            raise ValueError(f"case {entry_id}: expected_tickers must be a list")
        cases.append(EntityResolutionCase(**{f: entry[f] for f in _REQUIRED}))
    return cases


def run_entity_resolution_eval(
    detector, companies: list[dict], cases: list[EntityResolutionCase]
) -> dict:
    """Score a CompanyDetector against hand-labeled cases.

    Unlike golden.yaml/harness.py, no retrieval is involved -- this judges
    the detector's raw output directly, so order never matters (a question
    naming two companies in either order is equally correct).
    """
    correct = 0
    mismatches = []
    for case in cases:
        actual = detector.detect(case.question, companies)
        if set(actual) == set(case.expected_tickers):
            correct += 1
        else:
            mismatches.append(
                {"id": case.id, "expected": case.expected_tickers, "actual": actual}
            )
    n = len(cases)
    return {
        "cases": n,
        "correct": correct,
        "accuracy": round(correct / n, 4) if n else 0.0,
        "mismatches": mismatches,
    }
