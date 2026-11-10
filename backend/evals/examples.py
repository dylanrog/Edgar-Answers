"""Release check for the demo's example questions (spec 2026-11-09 §6).

Each example runs several times through the real production /ask path with
empty filters and is judged against pinned gold: right filing, right
sentences, right figures. "Every quote verified" is not enough -- a verified
quote from the wrong quarter is exactly the failure this exists to catch.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from api.answer import answer_stream

from .faithfulness import _REFUSALS, values_present
from .harness import GoldenQuestion, load_golden

EXAMPLES_PATH = Path(__file__).parent / "examples.yaml"
FRONTEND_EXAMPLES_PATH = (
    Path(__file__).resolve().parents[2] / "frontend" / "lib" / "examples.json"
)


def load_examples() -> dict[str, list[GoldenQuestion]]:
    groups: dict[str, list[GoldenQuestion]] = defaultdict(list)
    for question in load_golden(EXAMPLES_PATH):
        groups[question.group].append(question)
    return dict(groups)


def judge_run(
    entries: list[GoldenQuestion],
    kind: str,
    answer: str,
    citations: list[dict],
    unverified_answer: bool,
    errored: bool,
) -> list[str]:
    """Failure reasons for one run; [] means it passed."""
    if errored:
        return ["stream errored"]
    reasons: list[str] = []
    lowered = answer.lower()
    if not answer.strip() or any(phrase in lowered for phrase in _REFUSALS):
        reasons.append("refused or empty answer")
    if not citations:
        reasons.append("no citations")
    if unverified_answer:
        reasons.append("answer flagged unverified")
    if any(not c["verified"] for c in citations):
        reasons.append("a citation failed verification")
    verified = [c for c in citations if c["verified"]]
    for e in entries:
        if not any(
            c["accession"] == e.accession and set(c["sids"]) & set(e.gold_sids)
            for c in verified
        ):
            wrong = sorted(
                {c["accession"] for c in verified if c["ticker"] == e.ticker}
                - {e.accession}
            )
            hint = f" (cited {', '.join(wrong)} instead)" if wrong else ""
            reasons.append(
                f"{e.ticker}: no verified citation hits {e.accession} gold sids{hint}"
            )
        if e.expected_values and not values_present(answer, e.expected_values):
            reasons.append(f"{e.ticker}: expected values missing from answer")
    if kind == "table" and not any(c.get("cells") for c in citations):
        reasons.append("table example cited no cells")
    if kind == "comparison" and len({c["ticker"] for c in verified}) < 2:
        reasons.append("comparison cited fewer than 2 tickers")
    return reasons


def _kinds() -> dict[str, str]:
    entries = json.loads(FRONTEND_EXAMPLES_PATH.read_text(encoding="utf-8"))
    return {e["id"]: e["kind"] for e in entries}


def run_examples(
    conn, embedder, generator, company_detector, period_detector, query_rewriter,
    *, runs: int = 5,
) -> bool:
    kinds = _kinds()
    all_ok = True
    for group, entries in load_examples().items():
        passed = 0
        for n in range(1, runs + 1):
            parts: list[str] = []
            citations: list[dict] = []
            unverified = errored = False
            # Exactly the arguments /ask passes with empty filters.
            for event in answer_stream(
                conn, embedder, generator, company_detector, entries[0].question,
                tickers=None, form_type=None, period_detector=period_detector,
                query_rewriter=query_rewriter, conversation_id=None,
            ):
                if event.name == "token":
                    parts.append(event.data["text"])
                elif event.name == "citation":
                    citations.append(event.data)
                elif event.name == "done":
                    unverified = bool(event.data["unverified_answer"])
                elif event.name == "error":
                    errored = True
                    break
            reasons = judge_run(
                entries, kinds.get(group, "prose"), "".join(parts), citations,
                unverified, errored,
            )
            passed += not reasons
            print(f"{'PASS' if not reasons else 'FAIL'} {group} run {n}"
                  + (f": {'; '.join(reasons)}" if reasons else ""))
        print(f"{group}: {passed}/{runs}")
        all_ok &= passed == runs
    print("ALL PASSED" if all_ok else "FAILURES")
    return all_ok
