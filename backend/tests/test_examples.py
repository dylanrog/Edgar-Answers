import json

from evals.examples import FRONTEND_EXAMPLES_PATH, judge_run, load_examples
from evals.harness import GoldenQuestion


def entry(ticker="AAPL", accession="0000320193-24-000123", sids=(10,), values=(("383,285",),)):
    return GoldenQuestion(
        id=f"e-{ticker}", question="q?", ticker=ticker, accession=accession,
        section="item7", gold_sids=list(sids), group="g", expected_values=values,
    )


def cite(ticker="AAPL", accession="0000320193-24-000123", sids=(10,), verified=True, cells=()):
    return {"ticker": ticker, "accession": accession, "sids": list(sids),
            "verified": verified, "cells": list(cells)}


GOOD = "Net sales were $383,285 million."


def judge(entries=None, kind="prose", answer=GOOD, citations=None, unverified=False, errored=False):
    return judge_run(
        entries or [entry()], kind, answer,
        [cite()] if citations is None else citations, unverified, errored,
    )


def test_clean_run_passes():
    assert judge() == []


def test_refusal_fails():
    reasons = judge(answer="The filings do not contain this.")
    assert any("refus" in r for r in reasons)


def test_no_citations_fails():
    assert judge(citations=[])


def test_unverified_citation_fails():
    assert judge(citations=[cite(), cite(verified=False)])


def test_unverified_answer_fails():
    assert judge(unverified=True)


def test_errored_fails():
    assert judge(errored=True)


def test_right_ticker_wrong_accession_names_ticker():
    reasons = judge(citations=[cite(accession="0000320193-25-000001")])
    assert any("AAPL" in r for r in reasons)


def test_missing_values_fails():
    assert judge(answer="Net sales rose.")


def test_table_needs_cells():
    assert judge(kind="table")
    assert judge(kind="table", citations=[cite(cells=[{"sid": 10, "cell": 1}])]) == []


def test_comparison_needs_two_tickers():
    msft = entry("MSFT", "0001193125-26-323660", (5,), (("1",),))
    both = [cite(), cite("MSFT", "0001193125-26-323660", (5,))]
    answer = GOOD + " 1"
    assert judge([entry(), msft], "comparison", answer, both) == []
    # MSFT citation present but belongs to AAPL's filing too -> only one ticker
    assert judge([entry(), entry(values=())], "comparison", GOOD, [cite()])


def test_frontend_examples_match_examples_yaml():
    front = json.loads(FRONTEND_EXAMPLES_PATH.read_text(encoding="utf-8"))
    groups = load_examples()
    assert {e["id"] for e in front} == set(groups)
    assert {(e["id"], e["question"]) for e in front} == {
        (g, q[0].question) for g, q in groups.items()
    }
    if front:
        featured = [e for e in front if e["featured"]]
        assert len(featured) == 1 and featured[0]["kind"] == "table"
    kinds = {e["id"]: e["kind"] for e in front}
    for group, entries in groups.items():
        if kinds[group] in ("table", "comparison"):
            assert all(e.expected_values for e in entries), group
        if kinds[group] == "comparison":
            assert len({e.ticker for e in entries}) >= 2, group
