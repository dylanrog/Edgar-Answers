from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CASES_PATH = Path(__file__).parent / "fiscal_period_cases.yaml"
_REQUIRED = ("id", "question", "ticker", "expected_accessions")


@dataclass(frozen=True)
class PeriodCase:
    id: str
    question: str
    ticker: str
    expected_accessions: list[str]


def load_cases(path: Path = CASES_PATH) -> list[PeriodCase]:
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    cases = []
    for entry in entries:
        entry_id = entry.get("id", "<missing id>")
        for field in _REQUIRED:
            if field not in entry:
                raise ValueError(f"case {entry_id}: missing field {field!r}")
        if not isinstance(entry["expected_accessions"], list):
            raise ValueError(f"case {entry_id}: expected_accessions must be a list")
        cases.append(PeriodCase(**{f: entry[f] for f in _REQUIRED}))
    return cases


def run_period_resolution_eval(
    detector, filings_by_ticker: dict[str, list[dict]], cases: list[PeriodCase]
) -> dict:
    """Score a PeriodDetector against hand-labeled cases.

    Like entity_resolution.run_entity_resolution_eval, no retrieval is
    involved -- this judges the detector's raw output directly, so order
    never matters.
    """
    correct = 0
    mismatches = []
    for case in cases:
        filings = filings_by_ticker[case.ticker]
        actual = detector.detect(case.question, case.ticker, filings)
        if set(actual) == set(case.expected_accessions):
            correct += 1
        else:
            mismatches.append(
                {"id": case.id, "expected": case.expected_accessions, "actual": actual}
            )
    n = len(cases)
    return {
        "cases": n,
        "correct": correct,
        "accuracy": round(correct / n, 4) if n else 0.0,
        "mismatches": mismatches,
    }
