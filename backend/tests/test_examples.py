import json

import pytest
from evals import examples as ex
from evals.examples import TUTORIAL_PATH, chains, judge_run, load_examples
from evals.harness import GoldenQuestion, load_golden


def entry(ticker="AAPL", accession="0000320193-24-000123", sids=(10,), values=(("383,285",),),
          requires_cell=False, gold_cells=()):
    return GoldenQuestion(
        id=f"e-{ticker}", question="q?", ticker=ticker, accession=accession,
        section="item7", gold_sids=list(sids), group="g", expected_values=values,
        requires_cell=requires_cell, gold_cells=tuple(gold_cells),
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


def test_stray_verified_citation_to_another_accession_fails():
    stray = cite("NVDA", "0001045810-25-000023", (10,))
    reasons = judge(citations=[cite(), stray])
    assert any("stray" in r and "0001045810-25-000023" in r for r in reasons)


def test_extra_verified_citation_from_the_pinned_filing_passes():
    # A further true fact from the right filing (turn 1's Compute/Networking
    # rows) is not stray.
    assert judge(citations=[cite(), cite(sids=(99,))]) == []


def test_neighbour_row_in_the_pinned_filing_passes_when_its_cells_are_stated_or_dropped():
    # The gold row's cell is the right column; the neighbour row carries a
    # stated figure's cell, or none at all (narrowing dropped them).
    e = entry(gold_cells=(2,))
    stated = cite(sids=(10, 11), cells=[{"sid": 10, "cell": 2}, {"sid": 11, "cell": 1}])
    dropped = cite(sids=(10, 11), cells=[{"sid": 10, "cell": 2}])
    assert judge([e], citations=[stated]) == []
    assert judge([e], citations=[dropped]) == []


def test_neighbour_row_still_fails_when_a_wrong_year_is_marked_on_the_gold_row():
    e = entry(gold_cells=(2,))
    c = cite(sids=(10, 11), cells=[{"sid": 10, "cell": 6}])
    assert any("outside gold_cells" in r for r in judge([e], citations=[c]))


def test_tutorial_segment_breakdown_citation_passes():
    # Turn 1's live third claim, after narrowing dropped the Data Center
    # row's cells: [3] covers 2768-2770 and marks Compute and Networking.
    acc = "0001045810-25-000023"
    nvda = entry("NVDA", acc, (2768,), (("115.2 billion",),), True, (2,))
    first = cite("NVDA", acc, (2768,), cells=[{"sid": 2768, "cell": 2}])
    third = cite("NVDA", acc, (2768, 2769, 2770),
                 cells=[{"sid": 2769, "cell": 1}, {"sid": 2770, "cell": 1}])
    answer = "NVIDIA's Data Center revenue was $115.2 billion [1]. [3] Compute and Networking."
    assert judge([nvda], answer=answer, citations=[first, third]) == []


def test_extra_citation_from_an_unpinned_filing_fails():
    other = cite(accession="0000320193-25-000001", sids=(10,))
    reasons = judge(citations=[cite(), other])
    assert any("stray" in r and "0000320193-25-000001" in r for r in reasons)


def test_extra_citation_from_the_pinned_filing_fails_when_the_entry_misses():
    # No pin passes (figure not stated), so only the own pin's gold sids are
    # allowed; the neighbour row is stray as well as the entry being a miss.
    reasons = judge(answer="Net sales rose.", citations=[cite(), cite(sids=(99,))])
    assert any("stray" in r and "sids=[99]" in r for r in reasons)
    assert any("expected values missing" in r for r in reasons)


def test_citation_on_a_subset_of_gold_sids_passes():
    gold = entry(sids=(10, 11))
    assert judge([gold], citations=[cite(sids=(10,)), cite(sids=(10, 11))]) == []


def test_duplicate_citation_of_the_same_gold_sentence_passes():
    assert judge(citations=[cite(), cite()]) == []


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


def test_gold_cell_exactly_passes():
    e = entry(requires_cell=True, gold_cells=(2,))
    assert judge([e], citations=[cite(cells=[{"sid": 10, "cell": 2}])]) == []


def test_extra_column_on_gold_row_fails():
    # "Data Center $ 115,186 $ 47,525" verifies and marks FY2024 beside FY2025.
    e = entry(gold_cells=(2,))
    c = cite(cells=[{"sid": 10, "cell": 2}, {"sid": 10, "cell": 6}])
    reasons = judge([e], citations=[c])
    assert any("outside gold_cells" in r and "[6]" in r for r in reasons)


def test_wrong_column_on_gold_row_fails():
    e = entry(gold_cells=(2,))
    reasons = judge([e], citations=[cite(cells=[{"sid": 10, "cell": 6}])])
    assert any("outside gold_cells" in r for r in reasons)


def test_requires_cell_fails_when_only_another_column_is_cited():
    e = entry(requires_cell=True, gold_cells=(2,))
    reasons = judge([e], citations=[cite(cells=[{"sid": 10, "cell": 6}])])
    assert any("no verified citation" in r and "gold cell" in r for r in reasons)


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


def test_prints_each_turns_resolved_question(monkeypatch, tmp_path, capsys):
    _two_turn_yaml(monkeypatch, tmp_path)
    _fake_stream(monkeypatch, [])
    ex.run_examples(_Conn(), None, None, None, None, None, None, runs=1)
    out = capsys.readouterr().out
    assert "  [t2] resolved: What was it in fiscal 2024?" in out
    assert "[t1] resolved" not in out


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


def test_load_golden_gold_cells_default_empty_and_read(tmp_path):
    text = _BASE.format(after="g1") + "  gold_cells: [2]\n"
    qs = load_golden(_write(tmp_path, text))
    assert (qs[0].gold_cells, qs[1].gold_cells) == ((), (2,))


@pytest.mark.parametrize("bad", ["2", "[\"2\"]", "[2.5]", "[true]"])
def test_load_golden_gold_cells_must_be_list_of_ints(tmp_path, bad):
    text = _BASE.format(after="g1") + f"  gold_cells: {bad}\n"
    with pytest.raises(ValueError, match="gold_cells"):
        load_golden(_write(tmp_path, text))


def test_examples_pin_the_nvda_year_column():
    nvda = [q for qs in load_examples().values() for q in qs if q.ticker == "NVDA"]
    assert nvda and all(q.gold_cells == (2,) for q in nvda)


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
        if question == "What about 24?":
            yield _Ev("resolved", {"standalone_question": "What was it in fiscal 2024?"})
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


# --- alternatives: a later filing's restated figure for the same period ---

RESTATED = "0001193125-26-323660"


def msft(alternatives=()):
    from evals.harness import Alternative

    return GoldenQuestion(
        id="e-MSFT", question="q?", ticker="MSFT", accession="0000950170-24-087843",
        section="item7", gold_sids=[3161], group="g",
        expected_values=(("137.4 billion", "137.4"),),
        alternatives=tuple(
            Alternative(RESTATED, [2907], (("137.7 billion", "137.7"),), *rest)
            for rest in alternatives
        ),
    )


def msft_cite(accession=RESTATED, sids=(2907,), cells=()):
    return cite("MSFT", accession, sids, cells=cells)


def test_alternative_hit_with_its_figure_passes():
    answer = "Microsoft Cloud revenue was $137.7 billion in fiscal 2024."
    assert judge([msft([()])], answer=answer, citations=[msft_cite()]) == []


def test_alternative_hit_without_an_alternative_is_a_stray_and_a_miss():
    answer = "Microsoft Cloud revenue was $137.7 billion in fiscal 2024."
    reasons = judge([msft()], answer=answer, citations=[msft_cite()])
    assert any("stray" in r for r in reasons)
    assert any("no verified citation hits 0000950170-24-087843" in r for r in reasons)


def test_neither_primary_nor_alternative_hit_fails():
    other = msft_cite("0000950170-25-100235", (2869,))
    answer = "Microsoft Cloud revenue was $137.7 billion in fiscal 2024."
    reasons = judge([msft([()])], answer=answer, citations=[other])
    assert any("no verified citation hits 0000950170-24-087843" in r for r in reasons)


def test_a_citation_on_a_failing_alternative_is_stray():
    # The answer states $137.4B through its own pin but also cites the
    # restated filing, whose row shows $137.7B -- a figure the answer doesn't
    # give. The alternative itself fails, so its citation is not gold.
    answer = "Microsoft Cloud revenue was $137.4 billion in fiscal 2024."
    both = [msft_cite("0000950170-24-087843", (3161,)), msft_cite()]
    reasons = judge([msft([()])], answer=answer, citations=both)
    assert any("stray" in r and RESTATED in r for r in reasons)


def test_a_citation_on_a_passing_alternative_is_not_stray():
    answer = "Originally $137.4 billion, restated to $137.7 billion, in fiscal 2024."
    both = [msft_cite("0000950170-24-087843", (3161,)), msft_cite()]
    assert judge([msft([()])], answer=answer, citations=both) == []


def test_a_citation_on_a_failing_own_pin_is_stray_when_an_alternative_passes():
    # The answer states only the restated $137.7B (the alternative passes) but
    # also cites the original row showing $137.4B -- a figure it doesn't give.
    answer = "Microsoft Cloud revenue was $137.7 billion in fiscal 2024."
    both = [msft_cite("0000950170-24-087843", (3161,)), msft_cite()]
    reasons = judge([msft([()])], answer=answer, citations=both)
    assert any("stray" in r and "0000950170-24-087843" in r for r in reasons)


def _alt_yaml(monkeypatch, tmp_path):
    text = """
- id: t1-m
  group: t1
  question: What about 24?
  ticker: MSFT
  accession: "0000950170-24-087843"
  section: item7
  gold_sids: [3161]
  expected_values: [["137.4"]]
  alternatives:
    - accession: "0001193125-26-323660"
      gold_sids: [2907]
      expected_values: [["137.7"]]
"""
    monkeypatch.setattr(ex, "EXAMPLES_PATH", _write(tmp_path, text))


def _alt_stream(monkeypatch, narrowed):
    def fake(conn, embedder, generator, detector, question, **kw):
        filings = (
            [{"accession": "0000950170-24-087843", "ticker": "MSFT", "form_type": "10-K",
              "filing_date": "2024-07-30"}]
            if narrowed else []
        )
        yield _Ev("progress", {"stage": "targets", "tickers": ["MSFT"], "filings": filings})
        yield _Ev("token", {"text": "Microsoft Cloud revenue was $137.7 billion."})
        yield _Ev("citation", msft_cite())
        yield _Ev("done", {"unverified_answer": False})

    monkeypatch.setattr(ex, "answer_stream", fake)


def test_run_reports_which_pin_satisfied_and_the_targets(monkeypatch, tmp_path, capsys):
    _alt_yaml(monkeypatch, tmp_path)
    _alt_stream(monkeypatch, narrowed=True)
    assert ex.run_examples(_Conn(), None, None, None, None, None, None, runs=2) is True
    out = capsys.readouterr().out
    assert "  [t1] pins: t1-m=alt 1" in out
    assert "  [t1] targets: MSFT; filings: MSFT 10-K 2024-07-30 0000950170-24-087843" in out
    assert "t1-m: satisfied by an alternative in 2/2 runs (alt 1: 2)" in out


def test_run_reports_targets_that_were_not_narrowed(monkeypatch, tmp_path, capsys):
    _alt_yaml(monkeypatch, tmp_path)
    _alt_stream(monkeypatch, narrowed=False)
    ex.run_examples(_Conn(), None, None, None, None, None, None, runs=1)
    assert "  [t1] targets: MSFT; filings: not narrowed" in capsys.readouterr().out


def test_pins_used_labels_own_alternative_and_none():
    e = msft([()])
    own = msft_cite("0000950170-24-087843", (3161,))
    assert ex.pins_used([e], "It was $137.4 billion.", [own]) == {"e-MSFT": "own"}
    assert ex.pins_used([e], "It was $137.7 billion.", [msft_cite()]) == {"e-MSFT": "alt 1"}
    assert ex.pins_used([e], "It was $137.4 billion.", [msft_cite()]) == {"e-MSFT": "none"}


def test_the_hit_alternatives_values_are_the_ones_checked():
    # The restated filing is cited but the answer states the original figure:
    # the alternative's own expected value (137.7) is missing, so it fails.
    answer = "Microsoft Cloud revenue was $137.4 billion in fiscal 2024."
    reasons = judge([msft([()])], answer=answer, citations=[msft_cite()])
    assert any("expected values missing" in r for r in reasons)
    assert not any("no verified citation hits" in r for r in reasons)


def test_alternative_cell_rules_apply():
    answer = "Microsoft Cloud revenue was $137.7 billion in fiscal 2024."
    alt = msft([((2,), True)])  # gold_cells (2,), requires_cell
    assert judge([alt], answer=answer, citations=[msft_cite()])
    wrong = msft_cite(cells=[{"sid": 2907, "cell": 6}])
    assert any("outside gold_cells" in r for r in judge([alt], answer=answer, citations=[wrong]))
    right = msft_cite(cells=[{"sid": 2907, "cell": 2}])
    assert judge([alt], answer=answer, citations=[right]) == []


_ALT = """  alternatives:
    - accession: "2"
      gold_sids: [7]
      expected_values: [["1.5"]]
"""


def test_load_golden_reads_alternatives(tmp_path):
    qs = load_golden(_write(tmp_path, _BASE.format(after="g1") + _ALT))
    assert qs[0].alternatives == ()
    (alt,) = qs[1].alternatives
    assert (alt.accession, alt.gold_sids, alt.expected_values) == ("2", [7], (("1.5",),))
    assert (alt.gold_cells, alt.requires_cell) == ((), False)
    assert [p.accession for p in qs[1].pins()] == ["1", "2"]


_ALT_HEAD = '  alternatives:\n    - accession: "2"\n      gold_sids: [7]\n'


@pytest.mark.parametrize(
    "bad",
    [
        "  alternatives: {}\n",
        '  alternatives: ["2"]\n',
        "  alternatives:\n    - gold_sids: [7]\n",
        '  alternatives:\n    - accession: "2"\n',
        "  alternatives:\n    - accession: 2\n      gold_sids: [7]\n",
        _ALT_HEAD + "      expected_values: [[1.5]]\n",
        _ALT_HEAD + '      gold_cells: ["2"]\n',
        _ALT_HEAD + "      requires_cell: 1\n",
        _ALT_HEAD + "      ticker: MSFT\n",
    ],
)
def test_load_golden_rejects_malformed_alternatives(tmp_path, bad):
    with pytest.raises(ValueError, match="alternative"):
        load_golden(_write(tmp_path, _BASE.format(after="g1") + bad))


def test_examples_accept_msft_fiscal_2024_restated_in_later_filings():
    (msft_fy24,) = [q for q in load_examples()["tutorial-2"] if q.ticker == "MSFT"]
    assert {(a.accession, tuple(a.gold_sids)) for a in msft_fy24.alternatives} == {
        ("0001193125-26-323660", (2907,)),
        ("0000950170-25-100235", (2869,)),
    }
    assert all(a.expected_values == (("137.7 billion", "137.7"),)
               for a in msft_fy24.alternatives)


def test_verify_examples_checks_alternative_pins(monkeypatch, tmp_path, capsys):
    import argparse

    from evals import __main__ as cli

    queried = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            queried.append(params)

        def fetchone(self):
            return (1,) if len(queried[-1]) == 1 else ("a sentence",)

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(ex, "EXAMPLES_PATH", _write(tmp_path, _BASE.format(after="g1") + _ALT))
    monkeypatch.setattr(cli.db, "connect", Conn)
    cli.cmd_verify(argparse.Namespace(examples=True))
    assert ("2",) in queried and (1, 7) in queried
    assert "ok  b alt 1 sid 7" in capsys.readouterr().out
