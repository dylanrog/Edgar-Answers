from tests.fakes import StubCompanyDetector, StubPeriodDetector, StubQueryRewriter

from api.targets import Target, resolve_targets, retrieve_for_targets


def fake_retrieve_calls(monkeypatch, chunks_by_ticker):
    calls = []

    def fake_retrieve(
        conn, embedder, question, *, k_final, k_each=20, ticker=None, accessions=None,
        form_type=None,
    ):
        calls.append((question, ticker, accessions))
        return chunks_by_ticker.get(ticker, [])

    monkeypatch.setattr("api.targets.retrieve", fake_retrieve)
    return calls


def test_no_targets_falls_back_to_a_single_unscoped_retrieve(monkeypatch):
    calls = fake_retrieve_calls(monkeypatch, {None: ["unscoped-chunk"]})
    chunks = retrieve_for_targets(None, None, "q", [], k_final=8)
    assert chunks == ["unscoped-chunk"]
    assert calls == [("q", None, None)]


def test_each_target_gets_its_own_full_k_final_and_results_are_concatenated(monkeypatch):
    calls = fake_retrieve_calls(monkeypatch, {"MSFT": ["msft-chunk"], "AMZN": ["amzn-chunk"]})
    targets = [Target("MSFT", None), Target("AMZN", ["ACC-A"])]
    chunks = retrieve_for_targets(None, None, "q", targets, k_final=8)
    assert chunks == ["msft-chunk", "amzn-chunk"]
    assert calls == [("q", "MSFT", None), ("q", "AMZN", ["ACC-A"])]


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


def test_retrieve_for_targets_uses_a_targets_search_query_when_set(monkeypatch):
    calls = fake_retrieve_calls(monkeypatch, {"MSFT": ["msft-chunk"]})
    targets = [Target("MSFT", None, search_query="Microsoft revenue growth")]
    retrieve_for_targets(None, None, "original question", targets, k_final=8)
    assert calls == [("Microsoft revenue growth", "MSFT", None)]


def test_retrieve_for_targets_falls_back_to_the_question_when_search_query_is_none(
    monkeypatch,
):
    calls = fake_retrieve_calls(monkeypatch, {"MSFT": ["msft-chunk"]})
    targets = [Target("MSFT", None)]
    retrieve_for_targets(None, None, "original question", targets, k_final=8)
    assert calls == [("original question", "MSFT", None)]


def test_query_rewriter_fires_only_when_multiple_targets_are_resolved(monkeypatch):
    monkeypatch.setattr(
        "api.targets.queries.load_companies",
        lambda conn: [
            {"ticker": "MSFT", "name": "Microsoft Corporation"},
            {"ticker": "AMZN", "name": "Amazon.com, Inc."},
        ],
    )
    rewriter = StubQueryRewriter(
        {
            ("q", "MSFT"): "Microsoft revenue growth",
            ("q", "AMZN"): "Amazon revenue growth",
        }
    )
    targets = resolve_targets(
        None,
        "q",
        explicit_tickers=["MSFT", "AMZN"],
        company_detector=StubCompanyDetector(),
        query_rewriter=rewriter,
    )
    assert targets == [
        Target("MSFT", None, search_query="Microsoft revenue growth"),
        Target("AMZN", None, search_query="Amazon revenue growth"),
    ]
    assert set(rewriter.calls) == {
        ("q", "MSFT", "Microsoft Corporation"),
        ("q", "AMZN", "Amazon.com, Inc."),
    }


def test_query_rewriter_does_not_fire_for_a_single_target():
    rewriter = StubQueryRewriter()
    targets = resolve_targets(
        None,
        "q",
        explicit_tickers=["MSFT"],
        company_detector=StubCompanyDetector(),
        query_rewriter=rewriter,
    )
    assert targets == [Target("MSFT", None)]
    assert rewriter.calls == []


def test_query_rewriter_failure_degrades_to_no_search_query(monkeypatch):
    monkeypatch.setattr(
        "api.targets.queries.load_companies",
        lambda conn: [
            {"ticker": "MSFT", "name": "Microsoft Corporation"},
            {"ticker": "AMZN", "name": "Amazon.com, Inc."},
        ],
    )

    class BoomQueryRewriter:
        def rewrite(self, question, ticker, company_name):
            raise RuntimeError("rate limited")

    targets = resolve_targets(
        None,
        "q",
        explicit_tickers=["MSFT", "AMZN"],
        company_detector=StubCompanyDetector(),
        query_rewriter=BoomQueryRewriter(),
    )
    assert targets == [Target("MSFT", None), Target("AMZN", None)]


def test_no_query_rewriter_leaves_search_query_none():
    targets = resolve_targets(
        None,
        "q",
        explicit_tickers=["MSFT", "AMZN"],
        company_detector=StubCompanyDetector(),
    )
    assert targets == [Target("MSFT", None), Target("AMZN", None)]
