from tests.fakes import StubCompanyDetector, StubPeriodDetector

from api.targets import Target, resolve_targets, retrieve_for_targets


def fake_retrieve_calls(monkeypatch, chunks_by_ticker):
    calls = []

    def fake_retrieve(
        conn, embedder, question, *, k_final, k_each=20, ticker=None, accessions=None,
        form_type=None,
    ):
        calls.append((ticker, accessions))
        return chunks_by_ticker.get(ticker, [])

    monkeypatch.setattr("api.targets.retrieve", fake_retrieve)
    return calls


def test_no_targets_falls_back_to_a_single_unscoped_retrieve(monkeypatch):
    calls = fake_retrieve_calls(monkeypatch, {None: ["unscoped-chunk"]})
    chunks = retrieve_for_targets(None, None, "q", [], k_final=8)
    assert chunks == ["unscoped-chunk"]
    assert calls == [(None, None)]


def test_each_target_gets_its_own_full_k_final_and_results_are_concatenated(monkeypatch):
    calls = fake_retrieve_calls(monkeypatch, {"MSFT": ["msft-chunk"], "AMZN": ["amzn-chunk"]})
    targets = [Target("MSFT", None), Target("AMZN", ["ACC-A"])]
    chunks = retrieve_for_targets(None, None, "q", targets, k_final=8)
    assert chunks == ["msft-chunk", "amzn-chunk"]
    assert calls == [("MSFT", None), ("AMZN", ["ACC-A"])]


def test_explicit_tickers_are_normalized_but_never_dropped_for_being_unknown():
    targets = resolve_targets(
        None,
        "irrelevant",
        explicit_tickers=["msft", "NOPE", "msft"],
        company_detector=StubCompanyDetector(),
    )
    assert targets == [Target("MSFT", None), Target("NOPE", None)]


def test_blank_explicit_tickers_are_skipped_not_treated_as_unscoped():
    targets = resolve_targets(
        None,
        "irrelevant",
        explicit_tickers=["", "  ", "MSFT"],
        company_detector=StubCompanyDetector(),
    )
    assert targets == [Target("MSFT", None)]


def test_explicit_tickers_are_capped_at_max_companies():
    targets = resolve_targets(
        None,
        "irrelevant",
        explicit_tickers=["A", "B", "C", "D", "E"],
        company_detector=StubCompanyDetector(),
    )
    assert [t.ticker for t in targets] == ["A", "B", "C", "D"]


def test_no_explicit_tickers_runs_the_company_detector(monkeypatch):
    monkeypatch.setattr(
        "api.targets.queries.load_companies", lambda conn: [{"ticker": "MSFT"}]
    )
    detector = StubCompanyDetector({"how did msft do": ["MSFT"]})
    targets = resolve_targets(
        None, "how did msft do", explicit_tickers=None, company_detector=detector
    )
    assert targets == [Target("MSFT", None)]
    assert detector.calls == ["how did msft do"]


def test_company_detector_failure_degrades_to_no_targets(monkeypatch):
    monkeypatch.setattr("api.targets.queries.load_companies", lambda conn: [])

    class BoomDetector:
        def detect(self, question, companies):
            raise RuntimeError("network down")

    targets = resolve_targets(
        None, "q", explicit_tickers=None, company_detector=BoomDetector()
    )
    assert targets == []


def test_period_detector_fills_in_accessions_per_ticker(monkeypatch):
    monkeypatch.setattr(
        "api.targets.queries.load_filings_for_ticker",
        lambda conn, ticker: [{"accession": f"ACC-{ticker}"}],
    )
    period_detector = StubPeriodDetector({"q": ["ACC-MSFT"]})
    targets = resolve_targets(
        None,
        "q",
        explicit_tickers=["MSFT"],
        company_detector=StubCompanyDetector(),
        period_detector=period_detector,
    )
    assert targets == [Target("MSFT", ["ACC-MSFT"])]


def test_period_detector_failure_degrades_to_no_accessions(monkeypatch):
    monkeypatch.setattr(
        "api.targets.queries.load_filings_for_ticker",
        lambda conn, ticker: [{"accession": "ACC-1"}],
    )

    class BoomPeriodDetector:
        def detect(self, question, ticker, filings):
            raise RuntimeError("rate limited")

    targets = resolve_targets(
        None,
        "q",
        explicit_tickers=["MSFT"],
        company_detector=StubCompanyDetector(),
        period_detector=BoomPeriodDetector(),
    )
    assert targets == [Target("MSFT", None)]


def test_no_period_detector_leaves_accessions_none():
    targets = resolve_targets(
        None, "q", explicit_tickers=["MSFT"], company_detector=StubCompanyDetector()
    )
    assert targets == [Target("MSFT", None)]
