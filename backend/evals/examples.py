"""Release check for the demo's example questions (spec 2026-11-09 §6).

Each example runs several times through the real production /ask path with
empty filters and is judged against pinned gold: right filing, right
sentences, right figures. "Every quote verified" is not enough -- a verified
quote from the wrong quarter is exactly the failure this exists to catch.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from pathlib import Path

from api.answer import answer_stream

from .faithfulness import _REFUSALS, values_present
from .harness import GoldenQuestion, load_golden

EXAMPLES_PATH = Path(__file__).parent / "examples.yaml"
TUTORIAL_PATH = (
    Path(__file__).resolve().parents[2] / "frontend" / "lib" / "tutorial.json"
)


def load_examples() -> dict[str, list[GoldenQuestion]]:
    groups: dict[str, list[GoldenQuestion]] = defaultdict(list)
    for question in load_golden(EXAMPLES_PATH):
        groups[question.group].append(question)
    return dict(groups)


def judge_run(
    entries: list[GoldenQuestion],
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
    # Every verified citation must land on gold: a correct figure from the
    # right filing plus a stray one from another filing (or another sentence)
    # still shows the visitor a source that is not the answer.
    for c in verified:
        if not any(
            c["accession"] == e.accession and set(c["sids"]) & set(e.gold_sids)
            for e in entries
        ):
            reasons.append(
                f"stray verified citation {c.get('ticker')} {c['accession']}"
                f" sids={c['sids']} hits no gold entry"
            )
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
    for e in entries:
        # Figures the verified citations mark on this entry's gold rows. A gold
        # row can hold several years: a quote spanning two columns verifies and
        # highlights the wrong year too, so the column is checked, not just the row.
        on_gold = [
            cell["cell"]
            for c in verified
            if c["accession"] == e.accession
            for cell in c.get("cells") or []
            if cell["sid"] in e.gold_sids
        ]
        if e.gold_cells:
            outside = sorted(set(on_gold) - set(e.gold_cells))
            if outside:
                reasons.append(
                    f"{e.ticker}: verified citation on {e.accession} marks cell(s)"
                    f" {outside} on a gold sid, outside gold_cells {list(e.gold_cells)}"
                )
        if e.requires_cell and not any(
            not e.gold_cells or cell in e.gold_cells for cell in on_gold
        ):
            reasons.append(
                f"{e.ticker}: no verified citation on {e.accession} has a gold cell on a gold sid"
            )
    if len({e.ticker for e in entries}) >= 2 and len({c["ticker"] for c in verified}) < 2:
        reasons.append("comparison cited fewer than 2 tickers")
    return reasons


def chains() -> list[list[str]]:
    """Ordered group chains: a root followed by the groups that run `after` it."""
    groups = load_examples()
    after = {g: entries[0].after for g, entries in groups.items()}
    result: list[list[str]] = []
    for group in groups:
        if not after[group]:
            chain = [group]
            while nxt := next((g for g in groups if after[g] == chain[-1]), None):
                chain.append(nxt)
            result.append(chain)
    return result


def run_examples(
    conn, embedder, generator, company_detector, period_detector, query_rewriter,
    conversation_rewriter, *, runs: int = 10, verbose: bool = False,
) -> bool:
    groups = load_examples()
    all_ok = True
    for chain in chains():
        label = " -> ".join(chain)
        passed = 0
        for n in range(1, runs + 1):
            conversation_id = str(uuid.uuid4())
            failures: list[str] = []
            detail: list[tuple] = []
            for group in chain:
                entries = groups[group]
                parts: list[str] = []
                citations: list[dict] = []
                resolved = ""
                unverified = errored = False
                # Exactly the arguments /ask passes with empty filters.
                for event in answer_stream(
                    conn, embedder, generator, company_detector, entries[0].question,
                    tickers=None, form_type=None, period_detector=period_detector,
                    query_rewriter=query_rewriter, conversation_id=conversation_id,
                    conversation_rewriter=conversation_rewriter,
                ):
                    if event.name == "token":
                        parts.append(event.data["text"])
                    elif event.name == "resolved":
                        resolved = event.data["standalone_question"]
                    elif event.name == "citation":
                        citations.append(event.data)
                    elif event.name == "done":
                        unverified = bool(event.data["unverified_answer"])
                    elif event.name == "error":
                        errored = True
                        break
                conn.commit()
                answer = "".join(parts)
                turn_failures = [
                    f"{group}: {r}"
                    for r in judge_run(entries, answer, citations, unverified, errored)
                ]
                failures += turn_failures
                detail.append(
                    (group, resolved, answer, citations, bool(turn_failures))
                )
            print(f"{'PASS' if not failures else 'FAIL'} {label} run {n}"
                  + (f": {'; '.join(failures)}" if failures else ""))
            for group, resolved, answer, citations, turn_failed in detail:
                # How a follow-up was read in context; the first turn has none.
                if resolved:
                    print(f"  [{group}] resolved: {resolved}")
                for c in citations:
                    print(f"  [{group}] cited {c.get('ticker')} {c.get('accession')}"
                          f" sids={c.get('sids')} cells={c.get('cells')}")
                if turn_failed or verbose:
                    text = " ".join(answer.split())
                    if len(text) > 600:
                        text = text[:600] + "..."
                    print(f"  [{group}] answer: {text}")
            passed += not failures
        print(f"{label}: {passed}/{runs}")
        all_ok &= passed == runs
    print("ALL PASSED" if all_ok else "FAILURES")
    return all_ok
