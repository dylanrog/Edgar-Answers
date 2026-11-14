"""Release check for the demo's example questions (spec 2026-11-09 §6).

Each example runs several times through the real production /ask path with
empty filters and is judged against pinned gold: right filing, right
sentences, right figures. "Every quote verified" is not enough -- a verified
quote from the wrong quarter is exactly the failure this exists to catch.
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
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
    judged = {e.id: _judge_pins(e, verified, answer) for e in entries}
    # Every verified citation must come from a filing that answers the turn: a
    # correct figure plus a citation to some other filing still shows the
    # visitor a source that is not the answer. A pin counts only when it
    # passes on its own -- citing a restated $137.7B row beside an answer that
    # states only $137.4B (or the reverse) shows a figure the answer doesn't
    # give. Within a passing pin's filing any verified quote is fine: it is a
    # true fact from the right filing (turn 1 sometimes adds the Data Center
    # segment's Compute and Networking rows), and the gold_cells check below
    # still catches a wrong year marked on a gold row. When no pin of an entry
    # passes, its own pin's gold sids stay allowed, so the failure is reported
    # as a miss rather than also as a stray.
    passing = {
        pin.accession
        for e in entries
        for pin, pin_reasons in judged[e.id]
        if not pin_reasons
    }
    missed = [
        judged[e.id][0][0]
        for e in entries
        if all(pin_reasons for _, pin_reasons in judged[e.id])
    ]
    for c in verified:
        if c["accession"] not in passing and not any(
            c["accession"] == pin.accession and set(c["sids"]) <= set(pin.gold_sids)
            for pin in missed
        ):
            reasons.append(
                f"stray verified citation {c.get('ticker')} {c['accession']}"
                f" sids={c['sids']} is not from a passing pinned filing"
            )
    for e in entries:
        # The column check applies to every pin a citation lands on, satisfied
        # or not: a wrong year marked on a gold row is shown to the visitor.
        for pin in e.pins():
            outside = sorted(set(_on_gold(verified, pin)) - set(pin.gold_cells))
            if pin.gold_cells and outside:
                reasons.append(
                    f"{e.ticker}: verified citation on {pin.accession} marks cell(s)"
                    f" {outside} on a gold sid, outside gold_cells {list(pin.gold_cells)}"
                )
        # The entry is satisfied by its own pin OR any alternative. If none is,
        # report the first pin that was cited at all (else the entry's own), so
        # the reasons describe the filing the answer actually used.
        pins = judged[e.id]
        if all(pin_reasons for _, pin_reasons in pins):
            cited = [r for pin, r in pins if _hits(verified, pin)]
            reasons += cited[0] if cited else pins[0][1]
    if len({e.ticker for e in entries}) >= 2 and len({c["ticker"] for c in verified}) < 2:
        reasons.append("comparison cited fewer than 2 tickers")
    return reasons


def _judge_pins(
    e: GoldenQuestion, verified: list[dict], answer: str
) -> list[tuple[Alternative, list[str]]]:
    """Each pin of the entry (its own first) with the reasons it fails."""
    pins = e.pins()
    wrong = sorted(
        {c["accession"] for c in verified if c["ticker"] == e.ticker}
        - {pin.accession for pin in pins}
    )
    hint = f" (cited {', '.join(wrong)} instead)" if wrong else ""
    return [(pin, _pin_reasons(e.ticker, pin, verified, answer, hint)) for pin in pins]


def pins_used(entries: list[GoldenQuestion], answer: str, citations: list[dict]) -> dict:
    """Which pin satisfied each entry: "own", "alt N" (1-based), or "none"."""
    verified = [c for c in citations if c["verified"]]
    used = {}
    for e in entries:
        passing = [n for n, (_, r) in enumerate(_judge_pins(e, verified, answer)) if not r]
        if not passing:
            used[e.id] = "none"
        else:
            used[e.id] = "own" if passing[0] == 0 else f"alt {passing[0]}"
    return used


def _describe_targets(targets: dict) -> str:
    """The progress `targets` event: which companies, and which filings the
    period narrowed to (none means every filing of those companies)."""
    tickers = ", ".join(targets.get("tickers") or []) or "none"
    filings = [
        f"{f.get('ticker')} {f.get('form_type')} {f.get('filing_date')} {f.get('accession')}"
        for f in targets.get("filings") or []
    ]
    return f"{tickers}; filings: {', '.join(filings) if filings else 'not narrowed'}"


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
    conversation_rewriter, *, runs: int = 10, verbose: bool = False, quote_repairer=None,
) -> bool:
    groups = load_examples()
    all_ok = True
    # Citations the repair step re-quoted into verification (design §6.3 step
    # 5): a pass that leans on repair is still a pass, but the bracket should
    # see how often it fires.
    repaired_citations = verified_citations = total_citations = 0
    for chain in chains():
        label = " -> ".join(chain)
        passed = 0
        # entry id -> which pin satisfied it, per run (entries with alternatives)
        pin_use: dict[str, Counter] = defaultdict(Counter)
        for n in range(1, runs + 1):
            conversation_id = str(uuid.uuid4())
            failures: list[str] = []
            detail: list[tuple] = []
            for group in chain:
                entries = groups[group]
                parts: list[str] = []
                citations: list[dict] = []
                resolved = ""
                targets: dict | None = None
                unverified = errored = False
                # Exactly the arguments /ask passes with empty filters.
                for event in answer_stream(
                    conn, embedder, generator, company_detector, entries[0].question,
                    tickers=None, form_type=None, period_detector=period_detector,
                    query_rewriter=query_rewriter, conversation_id=conversation_id,
                    conversation_rewriter=conversation_rewriter,
                    quote_repairer=quote_repairer,
                ):
                    if event.name == "token":
                        parts.append(event.data["text"])
                    elif event.name == "resolved":
                        resolved = event.data["standalone_question"]
                    elif event.name == "progress" and event.data.get("stage") == "targets":
                        targets = event.data
                    elif event.name == "citation":
                        citations.append(event.data)
                    elif event.name == "done":
                        unverified = bool(event.data["unverified_answer"])
                    elif event.name == "error":
                        errored = True
                        break
                conn.commit()
                answer = "".join(parts)
                repaired_citations += sum(bool(c.get("repaired")) for c in citations)
                verified_citations += sum(bool(c.get("verified")) for c in citations)
                total_citations += len(citations)
                turn_failures = [
                    f"{group}: {r}"
                    for r in judge_run(entries, answer, citations, unverified, errored)
                ]
                failures += turn_failures
                used = pins_used(entries, answer, citations)
                for e in entries:
                    if e.alternatives:
                        pin_use[e.id][used[e.id]] += 1
                detail.append(
                    (group, resolved, targets, answer, citations, used, bool(turn_failures))
                )
            print(f"{'PASS' if not failures else 'FAIL'} {label} run {n}"
                  + (f": {'; '.join(failures)}" if failures else ""))
            for group, resolved, targets, answer, citations, used, turn_failed in detail:
                # How a follow-up was read in context; the first turn has none.
                if resolved:
                    print(f"  [{group}] resolved: {resolved}")
                # Whether the period narrowed the search: a later filing's
                # restated figure is fine, but not if targeting missed the year.
                if targets is not None:
                    print(f"  [{group}] targets: {_describe_targets(targets)}")
                for c in citations:
                    print(f"  [{group}] cited {c.get('ticker')} {c.get('accession')}"
                          f" sids={c.get('sids')} cells={c.get('cells')}"
                          + (" (repaired)" if c.get("repaired") else ""))
                print(f"  [{group}] pins: "
                      + ", ".join(f"{entry_id}={pin}" for entry_id, pin in used.items()))
                if turn_failed or verbose:
                    text = " ".join(answer.split())
                    if len(text) > 600:
                        text = text[:600] + "..."
                    print(f"  [{group}] answer: {text}")
            passed += not failures
        print(f"{label}: {passed}/{runs}")
        for entry_id, counts in pin_use.items():
            alts = sum(v for k, v in counts.items() if k.startswith("alt"))
            breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
            print(f"  {entry_id}: satisfied by an alternative in {alts}/{runs} runs"
                  f" ({breakdown})")
        all_ok &= passed == runs
    first_pass = verified_citations - repaired_citations
    rate = first_pass / total_citations if total_citations else 0.0
    print(f"first_pass_verified_rate: {rate:.4f} ({first_pass}/{total_citations})")
    print(f"repaired_citations: {repaired_citations}")
    print("ALL PASSED" if all_ok else "FAILURES")
    return all_ok
