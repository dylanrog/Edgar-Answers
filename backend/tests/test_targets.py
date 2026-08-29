from api.targets import Target, retrieve_for_targets


def fake_retrieve_calls(monkeypatch, chunks_by_ticker):
    calls = []

    def fake_retrieve(
        conn, embedder, question, *, k_final, ticker=None, accessions=None, form_type=None
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
