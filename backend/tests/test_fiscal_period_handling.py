import pytest
from evals.fiscal_period_handling import (
    PeriodCase,
    load_cases,
    run_period_resolution_eval,
)
from tests.fakes import StubPeriodDetector


def test_load_cases_validates_fields(tmp_path):
    good = tmp_path / "cases.yaml"
    good.write_text(
        "- id: p001\n"
        "  question: What did Apple report for fiscal 2024?\n"
        "  ticker: AAPL\n"
        "  expected_accessions: [\"0000320193-24-000123\"]\n",
        encoding="utf-8",
    )
    cases = load_cases(good)
    assert cases[0].id == "p001"
    assert cases[0].ticker == "AAPL"
    assert cases[0].expected_accessions == ["0000320193-24-000123"]

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "- id: p002\n  question: Missing ticker and expected_accessions\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="p002"):
        load_cases(bad)


def test_run_period_resolution_eval_scores_exact_set_match():
    cases = [
        PeriodCase("p001", "Q2 FY2024 gross margin?", "AAPL", ["ACC-A"]),
        PeriodCase("p002", "What is Apple's business model?", "AAPL", []),
    ]
    detector = StubPeriodDetector(
        {
            "Q2 FY2024 gross margin?": ["ACC-A"],
            "What is Apple's business model?": ["ACC-WRONG"],
        }
    )
    metrics = run_period_resolution_eval(
        detector, {"AAPL": [{"accession": "ACC-A"}]}, cases
    )
    assert metrics["cases"] == 2
    assert metrics["correct"] == 1
    assert metrics["accuracy"] == 0.5
    assert metrics["mismatches"] == [
        {"id": "p002", "expected": [], "actual": ["ACC-WRONG"]}
    ]


def test_run_period_resolution_eval_passes_each_case_its_own_ticker_filings():
    cases = [
        PeriodCase("p001", "AAPL question", "AAPL", ["ACC-A"]),
        PeriodCase("p002", "MSFT question", "MSFT", ["ACC-M"]),
    ]
    detector = StubPeriodDetector({"AAPL question": ["ACC-A"], "MSFT question": ["ACC-M"]})
    filings_by_ticker = {
        "AAPL": [{"accession": "ACC-A"}],
        "MSFT": [{"accession": "ACC-M"}],
    }
    metrics = run_period_resolution_eval(detector, filings_by_ticker, cases)
    assert metrics["accuracy"] == 1.0
    assert detector.calls == [
        ("AAPL question", "AAPL", filings_by_ticker["AAPL"]),
        ("MSFT question", "MSFT", filings_by_ticker["MSFT"]),
    ]
