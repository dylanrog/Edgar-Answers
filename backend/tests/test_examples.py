import json

import pytest
from evals import examples as ex
from evals.examples import TUTORIAL_PATH, chains, judge_run, load_examples
from evals.harness import GoldenQuestion, load_golden


def entry(ticker="AAPL", accession="0000320193-24-000123", sids=(10,), values=(("383,285",),),
          requires_cell=False):
    return GoldenQuestion(
        id=f"e-{ticker}", question="q?", ticker=ticker, accession=accession,
        section="item7", gold_sids=list(sids), group="g", expected_values=values,
        requires_cell=requires_cell,
    )


def cite(ticker="AAPL", accession="0000320193-24-000123", sids=(10,), verified=True, cells=()):
    return {"ticker": ticker, "accession": accession, "sids": list(sids),
            "verified": verified, "cells": list(cells)}


GOOD = "Net sales were $383,285 million."


def judge(entries=None, answer=GOOD, citations=None, unverified=False, errored=False):
    return judge_run(
        entries or [entry()], answer,
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


def test_requires_cell_passes_with_cell_on_right_accession():
    e = entry(requires_cell=True)
    assert judge([e], citations=[cite(cells=[{"sid": 10, "cell": 1}])]) == []


def test_requires_cell_fails_without_cell():
    assert judge([entry(requires_cell=True)])


def test_requires_cell_fails_with_cell_on_other_accession():
    other = cite(accession="0000320193-25-000001", cells=[{"sid": 1, "cell": 1}])
    assert judge([entry(requires_cell=True)], citations=[cite(), other])


def test_requires_cell_fails_with_cell_on_non_gold_sid():
    c = cite(cells=[{"sid": 99, "cell": 1}])
    assert judge([entry(requires_cell=True)], citations=[c])


def test_bare_figure_spelling_whole_figure_only():
    from evals.faithfulness import values_present
    exp = (("168.9 billion", "168.9"),)
    assert values_present("Microsoft Cloud was $168.9B.", exp)
    assert not values_present("Microsoft Cloud was 168.95.", exp)


def test_fail_prints_answer_and_citations(monkeypatch, tmp_path, capsys):
    _two_turn_yaml(monkeypatch, tmp_path)
    _fake_stream(monkeypatch, [], fail_question="What about 24?")
    ex.run_examples(_Conn(), None, None, None, None, None, None, runs=1)
    out = capsys.readouterr().out
    assert "cited AAPL 0000320193-24-000123 sids=[10]" in out
    assert "answer: Net sales were $383,285 million. 1" in out


def test_pass_prints_answer_only_when_verbose(monkeypatch, tmp_path, capsys):
    _two_turn_yaml(monkeypatch, tmp_path)
    _fake_stream(monkeypatch, [])
    ex.run_examples(_Conn(), None, None, None, None, None, None, runs=1)
    quiet = capsys.readouterr().out
    assert "cited AAPL" in quiet and "answer:" not in quiet
    ex.run_examples(_Conn(), None, None, None, None, None, None, runs=1, verbose=True)
    assert "answer:" in capsys.readouterr().out


def test_comparison_needs_two_tickers():
    msft = entry("MSFT", "0001193125-26-323660", (5,), (("1",),))
    both = [cite(), cite("MSFT", "0001193125-26-323660", (5,))]
    answer = GOOD + " 1"
    assert judge([entry(), msft], answer=answer, citations=both) == []
    # multi-ticker group but only AAPL cited
    reasons = judge([entry(), msft], answer=answer, citations=[cite()])
    assert any("fewer than 2 tickers" in r for r in reasons)
    # single-ticker group: the comparison check does not apply
    assert judge([entry()]) == []


def _write(tmp_path, text):
    p = tmp_path / "g.yaml"
    p.write_text(text, encoding="utf-8")
    return p


_BASE = """
- id: a
  group: g1
  question: q1
  ticker: AAPL
  accession: "1"
  section: item7
  gold_sids: [1]
- id: b
  group: g2
  question: q2
  ticker: AAPL
  accession: "1"
  section: item7
  gold_sids: [1]
  after: {after}
  requires_cell: true
"""


def test_load_golden_reads_after_and_requires_cell(tmp_path):
    qs = load_golden(_write(tmp_path, _BASE.format(after="g1")))
    assert (qs[0].after, qs[0].requires_cell) == ("", False)
    assert (qs[1].after, qs[1].requires_cell) == ("g1", True)


def test_load_golden_after_unknown_group(tmp_path):
    with pytest.raises(ValueError, match="after"):
        load_golden(_write(tmp_path, _BASE.format(after="nope")))


def test_load_golden_after_self(tmp_path):
    with pytest.raises(ValueError, match="after"):
        load_golden(_write(tmp_path, _BASE.format(after="g2")))


def test_load_golden_after_defined_later(tmp_path):
    text = _BASE.format(after="g3") + """- id: c
  group: g3
  question: q3
  ticker: AAPL
  accession: "1"
  section: item7
  gold_sids: [1]
"""
    with pytest.raises(ValueError, match="after"):
        load_golden(_write(tmp_path, text))


def test_chains_order(monkeypatch, tmp_path):
    text = _BASE.format(after="g1") + """- id: c
  group: g3
  question: q3
  ticker: AAPL
  accession: "1"
  section: item7
  gold_sids: [1]
"""
    monkeypatch.setattr(ex, "EXAMPLES_PATH", _write(tmp_path, text))
    assert chains() == [["g1", "g2"], ["g3"]]


class _Ev:
    def __init__(self, name, data):
        self.name, self.data = name, data


def _fake_stream(monkeypatch, calls, fail_question=None):
    def fake(conn, embedder, generator, detector, question, **kw):
        calls.append((question, kw))
        yield _Ev("token", {"text": GOOD + " 1"})
        if question == fail_question:
            yield _Ev("error", {})
            return
        yield _Ev("citation", cite())
        yield _Ev("citation", cite("MSFT", "0001193125-26-323660", (5,)))
        yield _Ev("done", {"unverified_answer": False})

    monkeypatch.setattr(ex, "answer_stream", fake)


class _Conn:
    commits = 0

    def commit(self):
        self.commits += 1


def _two_turn_yaml(monkeypatch, tmp_path):
    text = """
- id: t1-a
  group: t1
  question: first?
  ticker: AAPL
  accession: "0000320193-24-000123"
  section: item7
  gold_sids: [10]
  expected_values: [["383,285"]]
- id: t1-m
  group: t1
  question: first?
  ticker: MSFT
  accession: "0001193125-26-323660"
  section: item7
  gold_sids: [5]
  expected_values: [["1"]]
- id: t2-a
  group: t2
  after: t1
  question: What about 24?
  ticker: AAPL
  accession: "0000320193-24-000123"
  section: item7
  gold_sids: [10]
  expected_values: [["383,285"]]
- id: t2-m
  group: t2
  after: t1
  question: What about 24?
  ticker: MSFT
  accession: "0001193125-26-323660"
  section: item7
  gold_sids: [5]
  expected_values: [["1"]]
"""
    monkeypatch.setattr(ex, "EXAMPLES_PATH", _write(tmp_path, text))


def test_run_examples_shares_conversation_id_per_run(monkeypatch, tmp_path, capsys):
    _two_turn_yaml(monkeypatch, tmp_path)
    calls = []
    _fake_stream(monkeypatch, calls)
    conn = _Conn()
    ok = ex.run_examples(conn, None, None, None, None, None, "REWRITER", runs=2)
    assert ok is True
    assert [q for q, _ in calls] == ["first?", "What about 24?"] * 2
    ids = [kw["conversation_id"] for _, kw in calls]
    assert ids[0] == ids[1] and ids[2] == ids[3] and ids[0] != ids[2]
    assert all(kw["conversation_rewriter"] == "REWRITER" for _, kw in calls)
    assert all(kw["tickers"] is None and kw["form_type"] is None for _, kw in calls)
    assert conn.commits == 4
    assert "t1 -> t2: 2/2" in capsys.readouterr().out


def test_failing_turn_two_fails_run(monkeypatch, tmp_path, capsys):
    _two_turn_yaml(monkeypatch, tmp_path)
    calls = []
    _fake_stream(monkeypatch, calls, fail_question="What about 24?")
    ok = ex.run_examples(_Conn(), None, None, None, None, None, None, runs=1)
    out = capsys.readouterr().out
    assert ok is False
    assert "FAIL" in out and "0/1" in out


def test_tutorial_json_matches_examples_yaml():
    tut = json.loads(TUTORIAL_PATH.read_text(encoding="utf-8"))
    groups = load_examples()
    assert chains() == [["tutorial-1", "tutorial-2"]]
    assert tut["question"] == groups["tutorial-1"][0].question
    assert tut["followUp"] == groups["tutorial-2"][0].question
    assert groups["tutorial-2"][0].after == "tutorial-1"
    assert all(e.expected_values for g in groups.values() for e in g)
    assert {e.ticker for e in groups["tutorial-1"]} == {"NVDA", "MSFT"}
