from pathlib import Path

from evals import repin
from evals.harness import GoldenQuestion

from pipeline.canonicalize import Sentence


def question(gold_sids):
    return GoldenQuestion("q001", "Q?", "AAPL", "ACC-1", "item7", gold_sids)


def test_proposes_the_row_that_contains_the_old_text():
    """The old gold sentence was a whole flattened table; the new one is a
    single row of it, so the mapping is old-contains-new, not equality."""
    snap = {
        "q001": {
            "accession": "ACC-1",
            "sids": [12],
            "texts": ["Americas $ 1 Total net sales $ 383,285"],
        }
    }
    new_sentences = [
        Sentence(40, "item7", "Americas $ 1", 0, 12, 3),
        Sentence(41, "item7", "Total net sales $ 383,285", 13, 38, 3),
    ]
    proposals = repin.propose_from_sentences([question([12])], snap, {"ACC-1": new_sentences})
    assert len(proposals) == 1
    assert proposals[0].resolved is True
    assert set(proposals[0].new_sids) == {40, 41}


def test_exact_text_match_maps_one_to_one():
    snap = {"q001": {"accession": "ACC-1", "sids": [12], "texts": ["Net sales grew."]}}
    new_sentences = [
        Sentence(7, "item7", "Something else.", 0, 15, None),
        Sentence(8, "item7", "Net sales grew.", 16, 31, None),
    ]
    proposals = repin.propose_from_sentences([question([12])], snap, {"ACC-1": new_sentences})
    assert proposals[0].new_sids == [8]
    assert proposals[0].resolved is True


def test_unmappable_entry_is_reported_not_guessed():
    snap = {"q001": {"accession": "ACC-1", "sids": [12], "texts": ["Vanished text."]}}
    new_sentences = [Sentence(0, "item7", "Nothing alike.", 0, 14, None)]
    proposals = repin.propose_from_sentences([question([12])], snap, {"ACC-1": new_sentences})
    assert proposals[0].resolved is False
    assert proposals[0].new_sids == []


def test_apply_rewrites_only_resolved_entries(tmp_path: Path):
    golden = tmp_path / "golden.yaml"
    golden.write_text(
        '- id: q001\n  question: "Q?"\n  ticker: AAPL\n  accession: "ACC-1"\n'
        '  section: "item7"\n  gold_sids: [12]\n'
        '- id: q002\n  question: "Q2?"\n  ticker: AAPL\n  accession: "ACC-1"\n'
        '  section: "item7"\n  gold_sids: [99]\n',
        encoding="utf-8",
    )
    proposals = [
        repin.Proposal("q001", [12], [40, 41], True, "contained"),
        repin.Proposal("q002", [99], [], False, "no match"),
    ]
    changed = repin.apply_to_golden(golden, proposals)
    assert changed == 1
    text = golden.read_text(encoding="utf-8")
    assert "[40, 41]" in text
    assert "[99]" in text
