import json
import os
from datetime import date

import psycopg
import pytest
from evals import harness
from evals.entity_resolution import (
    EntityResolutionCase,
    load_cases,
    run_entity_resolution_eval,
)
from evals.faithfulness import run_faithfulness_eval
from evals.harness import GoldenQuestion
from tests.fakes import FakeEmbedder, StubCompanyDetector

from api.retrieval import RetrievedChunk
from api.targets import Target


def chunk(accession, sid_start, sid_end):
    return RetrievedChunk(
        1,
        accession,
        "10-K",
        date(2024, 11, 1),
        "TSTC",
        "item1",
        sid_start,
        sid_end,
        "t",
        1,
        1.0,
    )


def test_hit_requires_matching_accession_and_sid_overlap():
    chunks = [chunk("ACC-1", 10, 20)]
    assert harness.hit(chunks, "ACC-1", [15]) is True
    assert harness.hit(chunks, "ACC-1", [21]) is False
    assert harness.hit(chunks, "ACC-2", [15]) is False


def test_load_golden_validates_fields(tmp_path):
    good = tmp_path / "golden.yaml"
    good.write_text(
        "- id: q001\n"
        "  question: What were net sales?\n"
        "  ticker: AAPL\n"
        '  accession: "0000320193-24-000123"\n'
        "  section: item7\n"
        "  gold_sids: [612, 613]\n",
        encoding="utf-8",
    )
    questions = harness.load_golden(good)
    assert questions[0].id == "q001"
    assert questions[0].gold_sids == [612, 613]

    bad = tmp_path / "bad.yaml"
    bad.write_text("- id: q002\n  question: Missing everything else\n", encoding="utf-8")
    with pytest.raises(ValueError, match="q002"):
        harness.load_golden(bad)


def test_append_results_writes_json_line(tmp_path):
    path = tmp_path / "results.jsonl"
    harness.append_results(path, {"recall@10": 0.9})
    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["recall@10"] == 0.9
    assert "git_sha" in record and "timestamp" in record
    # A run against uncommitted code isn't reproducible from its sha, so the
    # log has to say so rather than leave it to be inferred later.
    assert isinstance(record["git_dirty"], bool)


GOLDEN = GoldenQuestion(
    id="q001",
    question="Did Apple's management conclude its disclosure controls were effective?",
    ticker="AAPL",
    accession="ACC-1",
    section="item9a",
    gold_sids=[5],
)


def _two_arm_retrieve(calls):
    """Gold chunk survives only under the ticker filter.

    That is the real behavior of q004/q005/q008/q014 once nine other filers
    are in the index: their gold sentences are generic accounting boilerplate,
    so near-identical text from other companies crowds Apple's out of the top
    10 unless retrieval is scoped to the ticker the golden entry names.
    """

    def fake_retrieve(conn, embedder, question, *, k_each, k_final, ticker=None):
        calls.append(ticker)
        return [chunk("ACC-1" if ticker else "OTHER-1", 0, 10)]

    return fake_retrieve


def test_run_retrieval_eval_scopes_retrieval_to_the_golden_ticker(monkeypatch):
    calls = []
    monkeypatch.setattr("evals.harness.retrieve", _two_arm_retrieve(calls))
    metrics = harness.run_retrieval_eval(None, None, [GOLDEN])
    assert "AAPL" in calls
    assert metrics["recall@10"] == 1.0
    assert metrics["misses@10"] == []


def test_run_retrieval_eval_also_reports_the_unfiltered_arm(monkeypatch):
    # Unfiltered is what a user gets when they don't pick a ticker on /ask, so
    # dropping it would hide a real product behavior rather than fix it.
    calls = []
    monkeypatch.setattr("evals.harness.retrieve", _two_arm_retrieve(calls))
    metrics = harness.run_retrieval_eval(None, None, [GOLDEN])
    assert None in calls
    assert metrics["unfiltered_recall@10"] == 0.0
    assert metrics["unfiltered_misses@10"] == ["q001"]


@pytest.mark.db
def test_run_retrieval_eval_end_to_end():
    from datetime import date

    from pipeline import db, store
    from pipeline.canonicalize import CanonicalFiling, Sentence
    from pipeline.chunk import Chunk
    from pipeline.companies import Company
    from pipeline.edgar import FilingRef

    text = "The board approved a quarterly dividend increase of four percent."
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chunks WHERE filing_id IN"
                " (SELECT id FROM filings WHERE accession = 'TESTE-24-000001')"
            )
            cur.execute(
                "DELETE FROM sentences WHERE filing_id IN"
                " (SELECT id FROM filings WHERE accession = 'TESTE-24-000001')"
            )
            cur.execute("DELETE FROM filings WHERE accession = 'TESTE-24-000001'")
        conn.commit()
        company = Company(999999005, "TSTE", "Test Co E")
        sentence = Sentence(0, "item1", text, 0, len(text))
        canonical = CanonicalFiling(
            text, [sentence], f'<p><span data-sid="0">{text}</span></p>'
        )
        ref = FilingRef(
            cik=999999005,
            accession="TESTE-24-000001",
            form_type="10-K",
            filing_date=date(2024, 11, 1),
            period_end=None,
            primary_document="t.htm",
        )
        filing_id = store.store_filing(conn, company, ref, canonical)
        store.store_chunks(
            conn, filing_id, [Chunk("item1", 0, 0, text, 10)],
            FakeEmbedder().embed_texts([text]),
        )

        questions = [
            harness.GoldenQuestion(
                "q001", text, "TSTE", "TESTE-24-000001", "item1", [0]
            ),
            harness.GoldenQuestion(
                "q002", "entirely unrelated basket weaving query", "TSTE",
                "TESTE-24-000001", "item1", [99],  # sid that no chunk covers
            ),
        ]
        metrics = harness.run_retrieval_eval(conn, FakeEmbedder(), questions)
        assert metrics["questions"] == 2
        assert metrics["recall@10"] == 0.5
        assert metrics["misses@10"] == ["q002"]


def test_faithfulness_metrics_are_computed_from_the_event_stream(monkeypatch):
    question = GoldenQuestion(
        id="q001",
        question="What were total net sales?",
        ticker="AAPL",
        accession="0000320193-24-000123",
        section="item7",
        gold_sids=[41],
    )
    events = [
        ("token", {"text": "Net sales rose [1]."}),
        (
            "citation",
            {
                "marker": 1,
                "verified": True,
                "accession": "0000320193-24-000123",
                "sids": [41, 42],
                "quote": "q",
            },
        ),
        (
            "citation",
            {"marker": 2, "verified": False, "accession": "", "sids": [], "quote": "x"},
        ),
        (
            "done",
            {
                "chunks_retrieved": 8,
                "citations_total": 2,
                "citations_verified": 1,
                "unverified_answer": False,
            },
        ),
    ]

    class FakeEvent:
        def __init__(self, name, data):
            self.name, self.data = name, data

    monkeypatch.setattr(
        "evals.faithfulness.answer_stream",
        lambda *a, **k: (FakeEvent(n, d) for n, d in events),
    )

    metrics = run_faithfulness_eval(None, None, None, None, [question])
    assert metrics["questions"] == 1
    assert metrics["citations_total"] == 2
    assert metrics["verified_rate"] == 0.5
    assert metrics["gold_sid_hit_rate"] == 1.0
    assert metrics["answered_rate"] == 1.0
    assert metrics["unverified_answers"] == 0


def test_load_cases_validates_fields(tmp_path):
    good = tmp_path / "cases.yaml"
    good.write_text(
        "- id: e001\n"
        "  question: Compare Microsoft and Amazon's growth.\n"
        "  expected_tickers: [MSFT, AMZN]\n",
        encoding="utf-8",
    )
    cases = load_cases(good)
    assert cases[0].id == "e001"
    assert cases[0].expected_tickers == ["MSFT", "AMZN"]

    bad = tmp_path / "bad.yaml"
    bad.write_text("- id: e002\n  question: Missing expected_tickers\n", encoding="utf-8")
    with pytest.raises(ValueError, match="e002"):
        load_cases(bad)


def test_run_entity_resolution_eval_scores_exact_set_match():
    cases = [
        EntityResolutionCase("e001", "Compare MSFT and AMZN.", ["MSFT", "AMZN"]),
        EntityResolutionCase("e002", "What is a 10-K?", []),
    ]
    detector = StubCompanyDetector(
        {
            "Compare MSFT and AMZN.": ["MSFT", "AMZN"],
            "What is a 10-K?": ["AAPL"],
        }
    )
    metrics = run_entity_resolution_eval(detector, [], cases)
    assert metrics["cases"] == 2
    assert metrics["correct"] == 1
    assert metrics["accuracy"] == 0.5
    assert metrics["mismatches"] == [
        {"id": "e002", "expected": [], "actual": ["AAPL"]}
    ]


def test_run_entity_resolution_eval_ignores_ticker_order():
    cases = [EntityResolutionCase("e001", "Compare A and B.", ["AAPL", "AMZN"])]
    detector = StubCompanyDetector({"Compare A and B.": ["AMZN", "AAPL"]})
    metrics = run_entity_resolution_eval(detector, [], cases)
    assert metrics["accuracy"] == 1.0


def test_load_golden_defaults_group_to_the_entrys_own_id(tmp_path):
    path = tmp_path / "golden.yaml"
    path.write_text(
        "- id: q001\n"
        "  question: What were net sales?\n"
        "  ticker: AAPL\n"
        '  accession: "0000320193-24-000123"\n'
        "  section: item7\n"
        "  gold_sids: [612]\n",
        encoding="utf-8",
    )
    questions = harness.load_golden(path)
    assert questions[0].group == "q001"


def test_load_golden_reads_an_explicit_group(tmp_path):
    path = tmp_path / "golden.yaml"
    path.write_text(
        "- id: qc001a\n"
        "  group: qc001\n"
        "  question: Compare X and Y.\n"
        "  ticker: AAPL\n"
        '  accession: "ACC-1"\n'
        "  section: item7\n"
        "  gold_sids: [1]\n",
        encoding="utf-8",
    )
    questions = harness.load_golden(path)
    assert questions[0].group == "qc001"


def test_load_golden_rejects_a_group_with_mismatched_question_text(tmp_path):
    path = tmp_path / "golden.yaml"
    path.write_text(
        "- id: qc1a\n"
        "  group: qc1\n"
        "  question: Compare X and Y.\n"
        "  ticker: AAPL\n"
        '  accession: "ACC-1"\n'
        "  section: item7\n"
        "  gold_sids: [1]\n"
        "- id: qc1b\n"
        "  group: qc1\n"
        "  question: A DIFFERENT question entirely.\n"
        "  ticker: MSFT\n"
        '  accession: "ACC-2"\n'
        "  section: item7\n"
        "  gold_sids: [2]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="qc1"):
        harness.load_golden(path)


def test_targeted_arm_dedupes_by_group_and_scores_each_sibling_row(monkeypatch):
    from tests.fakes import StubCompanyDetector, StubQueryRewriter

    a = harness.GoldenQuestion("qc001a", "Compare X and Y.", "AAPL", "ACC-A", "item7", [1], "qc001")
    b = harness.GoldenQuestion("qc001b", "Compare X and Y.", "MSFT", "ACC-M", "item7", [2], "qc001")

    resolve_calls = []
    resolve_rewriters = []
    retrieve_calls = []
    rewriter = StubQueryRewriter()

    def fake_resolve_targets(
        conn,
        question,
        *,
        explicit_tickers,
        company_detector,
        period_detector=None,
        query_rewriter=None,
    ):
        resolve_calls.append(question)
        resolve_rewriters.append(query_rewriter)
        return [Target("AAPL", None), Target("MSFT", None)]

    def fake_retrieve_for_targets(
        conn, embedder, question, targets, *, k_final, k_each=20, form_type=None
    ):
        retrieve_calls.append(question)
        return [chunk("ACC-A", 0, 5), chunk("ACC-M", 0, 5)]

    # run_retrieval_eval always computes the scoped + unfiltered arms too, so
    # the real retrieve() (which would call embedder.embed_query on None)
    # needs a stand-in even though this test only asserts on targeted_*.
    monkeypatch.setattr("evals.harness.retrieve", lambda *a, **k: [])
    monkeypatch.setattr("evals.harness.resolve_targets", fake_resolve_targets)
    monkeypatch.setattr("evals.harness.retrieve_for_targets", fake_retrieve_for_targets)

    metrics = harness.run_retrieval_eval(
        None,
        None,
        [a, b],
        company_detector=StubCompanyDetector(),
        query_rewriter=rewriter,
    )
    assert resolve_calls == ["Compare X and Y."]  # deduped: one group, one resolve call
    assert resolve_rewriters == [rewriter]  # the harness threads it through
    assert retrieve_calls == ["Compare X and Y."]
    assert metrics["targeted_recall@10"] == 1.0
    assert metrics["targeted_misses@10"] == []


def test_targeted_arm_is_skipped_when_no_company_detector_is_given(monkeypatch):
    # Same reason as above: the scoped + unfiltered arms still run even when
    # company_detector is omitted, so retrieve() needs a stand-in.
    monkeypatch.setattr("evals.harness.retrieve", lambda *a, **k: [])
    metrics = harness.run_retrieval_eval(None, None, [GOLDEN])
    assert "targeted_recall@10" not in metrics
