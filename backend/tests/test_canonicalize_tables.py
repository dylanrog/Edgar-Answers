from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from pipeline.canonicalize import canonicalize

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def result():
    raw = (FIXTURES / "table_shapes.html").read_text(encoding="utf-8")
    return canonicalize(raw, "10-K")


def texts(result):
    return [s.text for s in result.sentences]


def test_each_table_row_is_its_own_sentence(result):
    assert "Americas $ 162,560" in texts(result)
    assert "Total net sales $ 383,285" in texts(result)


def test_a_row_is_never_split_into_several_sentences(result):
    """A row is not prose. pysbd would split it at unpredictable points, and a
    row that segments differently between runs moves sids under stored
    citations."""
    assert not any(t == "Total net sales $" for t in texts(result))
    assert sum(1 for t in texts(result) if "383,285" in t) == 1


def test_bare_and_div_wrapped_tables_behave_identically(result):
    assert "Bare table row $ 1,234" in texts(result)
    assert "Americas $ 162,560" in texts(result)


def test_spacer_rows_produce_no_sentence(result):
    assert all(t.strip() for t in texts(result))
    assert not any(t == "" for t in texts(result))


def test_loose_text_beside_a_table_is_not_lost(result):
    assert "Loose text beside a table." in texts(result)


def test_nested_table_text_is_indexed_once(result):
    assert sum(1 for t in texts(result) if "Inner cell" in t) == 1
    assert not any("Outer label Inner cell" in t for t in texts(result))


def test_table_rows_carry_a_table_id_and_prose_does_not(result):
    by_text = {s.text: s for s in result.sentences}
    assert by_text["Americas $ 162,560"].table_id is not None
    assert by_text["Total net sales $ 383,285"].table_id == by_text["Americas $ 162,560"].table_id
    assert by_text["Bare table row $ 1,234"].table_id != by_text["Americas $ 162,560"].table_id
    assert by_text["Net sales discussion follows."].table_id is None


def test_table_markup_survives_into_viewer_html(result):
    """The div-wrapped case used to be cleared and replaced by a span, so the
    filing rendered as a run-on paragraph of numbers."""
    soup = BeautifulSoup(result.viewer_html, "lxml")
    assert len(soup.find_all("table")) >= 3
    assert soup.find("td") is not None


def test_rows_carry_data_sid_on_the_tr(result):
    soup = BeautifulSoup(result.viewer_html, "lxml")
    rows = [tr for tr in soup.find_all("tr") if tr.has_attr("data-sid")]
    assert rows, "no row carried a data-sid"
    sids = {int(tr["data-sid"]) for tr in rows}
    row_sids = {s.sid for s in result.sentences if s.table_id is not None}
    assert sids == row_sids


def test_every_sid_appears_exactly_once_in_viewer_html(result):
    soup = BeautifulSoup(result.viewer_html, "lxml")
    marked = [int(el["data-sid"]) for el in soup.select("[data-sid]")]
    assert sorted(marked) == [s.sid for s in result.sentences]


def test_sids_are_sequential_and_offsets_align(result):
    assert [s.sid for s in result.sentences] == list(range(len(result.sentences)))
    for s in result.sentences:
        assert result.canonical_text[s.char_start:s.char_end] == s.text
