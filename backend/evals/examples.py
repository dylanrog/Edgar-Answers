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
from .harness import Alternative, GoldenQuestion, load_golden

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
    # Every verified citation must sit wholly on gold: a correct figure from
    # the right filing plus a stray one from another filing, or a quote that
    # spans a gold row and its neighbour (the viewer would highlight both),
    # still shows the visitor a source that is not the answer. An entry's
    # alternatives are gold too.
    for c in verified:
        if not any(
            c["accession"] == pin.accession and set(c["sids"]) <= set(pin.gold_sids)
            for e in entries
            for pin in e.pins()
        ):
            reasons.append(
                f"stray verified citation {c.get('ticker')} {c['accession']}"
                f" sids={c['sids']} is not within one gold entry"
            )
    for e in entries:
        pins = e.pins()
        # The column check applies to every pin a citation lands on, satisfied
        # or not: a wrong year marked on a gold row is shown to the visitor.
        for pin in pins:
            outside = sorted(set(_on_gold(verified, pin)) - set(pin.gold_cells))
            if pin.gold_cells and outside:
                reasons.append(
                    f"{e.ticker}: verified citation on {pin.accession} marks cell(s)"
                    f" {outside} on a gold sid, outside gold_cells {list(pin.gold_cells)}"
                )
        # The entry is satisfied by its own pin OR any alternative. If none is,
        # report the first pin that was cited at all (else the entry's own), so
        # the reasons describe the filing the answer actually used.
        wrong = sorted(
            {c["accession"] for c in verified if c["ticker"] == e.ticker}
            - {pin.accession for pin in pins}
        )
        hint = f" (cited {', '.join(wrong)} instead)" if wrong else ""
        judged = [(pin, _pin_reasons(e.ticker, pin, verified, answer, hint)) for pin in pins]
        if all(pin_reasons for _, pin_reasons in judged):
            cited = [r for pin, r in judged if _hits(verified, pin)]
            reasons += cited[0] if cited else judged[0][1]
    if len({e.ticker for e in entries}) >= 2 and len({c["ticker"] for c in verified}) < 2:
        reasons.append("comparison cited fewer than 2 tickers")
    return reasons


def _hits(verified: list[dict], pin: Alternative) -> bool:
    return any(
        c["accession"] == pin.accession and set(c["sids"]) & set(pin.gold_sids)
        for c in verified
    )


def _on_gold(verified: list[dict], pin: Alternative) -> list[int]:
    """Figures the verified citations mark on this pin's gold rows. A gold row
    can hold several years: a quote spanning two columns verifies and
    highlights the wrong year too, so the column is checked, not just the row."""
    return [
        cell["cell"]
        for c in verified
        if c["accession"] == pin.accession
        for cell in c.get("cells") or []
        if cell["sid"] in pin.gold_sids
    ]


def _pin_reasons(
    ticker: str, pin: Alternative, verified: list[dict], answer: str, hint: str = ""
) -> list[str]:
    """Why this one pin is not satisfied: hit, figures stated, cell cited."""
    reasons: list[str] = []
    if not _hits(verified, pin):
        reasons.append(f"{ticker}: no verified citation hits {pin.accession} gold sids{hint}")
    if pin.expected_values and not values_present(answer, pin.expected_values):
        reasons.append(f"{ticker}: expected values missing from answer")
    if pin.requires_cell and not any(
        not pin.gold_cells or cell in pin.gold_cells for cell in _on_gold(verified, pin)
    ):
        reasons.append(
            f"{ticker}: no verified citation on {pin.accession} has a gold cell on a gold sid"
        )
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
