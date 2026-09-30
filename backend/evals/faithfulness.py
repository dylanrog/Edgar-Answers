from __future__ import annotations

from api.answer import answer_stream
from api.normalize import normalize

from .harness import GoldenQuestion

# A refusal is a correct answer when the corpus does not cover the question
# (design §10), so "answered" is measured, never assumed to be the goal.
_REFUSALS = ("do not contain", "does not contain", "not covered", "cannot answer")


def values_present(answer: str, expected: tuple[tuple[str, ...], ...]) -> bool:
    """Spec §7.2: every required figure appears in at least one accepted
    spelling. Deterministic -- the same normalization citations use, so
    "$ 115,186" and "$115,186" count alike and no LLM judges anything."""
    haystack, _ = normalize(answer)
    return all(
        any(normalize(spelling)[0].strip() in haystack for spelling in spellings)
        for spellings in expected
    )


def run_faithfulness_eval(
    conn, embedder, generator, company_detector, questions: list[GoldenQuestion],
    *, period_detector=None,
) -> dict:
    """Full /ask path per golden question: % citations verified, % answered,
    and whether verified citations actually land on the gold sentences.

    Each question is scoped to its own known ticker explicitly (not run
    through auto-detection) -- this measures the same thing it always has,
    just through query decomposition's new explicit-tickers path rather
    than the old single ticker= keyword. `period_detector` is optional and
    keyword-only so existing callers are unaffected; pass it to make this
    eval exercise the same accession-pinning path production /ask uses.
    """
    answered = 0
    unverified_answers = 0
    total = 0
    verified = 0
    gold_hits = 0
    value_questions = 0
    value_hits = 0
    value_misses: list[str] = []
    for question in questions:
        text_parts: list[str] = []
        citations: list[dict] = []
        for event in answer_stream(
            conn,
            embedder,
            generator,
            company_detector,
            question.question,
            tickers=[question.ticker],
            period_detector=period_detector,
        ):
            if event.name == "token":
                text_parts.append(event.data["text"])
            elif event.name == "citation":
                citations.append(event.data)
            elif event.name == "done":
                unverified_answers += int(event.data["unverified_answer"])
            elif event.name == "error":
                citations = []
                break
        raw_answer = "".join(text_parts)
        answer = raw_answer.lower()
        if question.expected_values:
            value_questions += 1
            if values_present(raw_answer, question.expected_values):
                value_hits += 1
            else:
                value_misses.append(question.id)
        if answer and not any(phrase in answer for phrase in _REFUSALS):
            answered += 1
        total += len(citations)
        verified += sum(c["verified"] for c in citations)
        gold_hits += any(
            c["verified"]
            and c["accession"] == question.accession
            and set(c["sids"]) & set(question.gold_sids)
            for c in citations
        )
    n = len(questions) or 1
    metrics = {
        "questions": len(questions),
        "answered_rate": round(answered / n, 4),
        "citations_total": total,
        "verified_rate": round(verified / total, 4) if total else 0.0,
        "gold_sid_hit_rate": round(gold_hits / n, 4),
        "unverified_answers": unverified_answers,
    }
    if value_questions:
        metrics["value_questions"] = value_questions
        metrics["value_accuracy"] = round(value_hits / value_questions, 4)
        metrics["value_misses"] = value_misses
    return metrics
