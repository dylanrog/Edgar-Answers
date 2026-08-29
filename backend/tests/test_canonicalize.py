from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from pipeline.canonicalize import canonicalize

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def result():
    raw = (FIXTURES / "mini_10k.html").read_text(encoding="utf-8")
    return canonicalize(raw, "10-K")


def test_sentences_extracted_with_sections(result):
    texts = [s.text for s in result.sentences]
    assert "The Company designs smartphones and related services." in texts
    assert "It sells its products worldwide." in texts
    by_text = {s.text: s for s in result.sentences}
    assert by_text["The Company designs smartphones and related services."].section == "item1"
    assert by_text["Demand could differ from expectations."].section == "item1a"


def test_sids_sequential_and_offsets_align(result):
    assert [s.sid for s in result.sentences] == list(range(len(result.sentences)))
    for s in result.sentences:
        assert result.canonical_text[s.char_start:s.char_end] == s.text


def test_viewer_html_data_sids_align_with_sentences(result):
    """Prose sentences carry data-sid on a <span>; table rows carry it on the
    <tr> itself (a <span> cannot wrap <td> elements). Either way every sid
    appears exactly once in viewer_html (spec 2026-08-12 §6)."""
    viewer = BeautifulSoup(result.viewer_html, "lxml")
    tagged = viewer.select("[data-sid]")
    marked = {int(el["data-sid"]): el.get_text(" ", strip=True) for el in tagged}
    assert len(marked) == len(result.sentences)
    for s in result.sentences:
        assert marked[s.sid] == s.text


def test_table_rows_are_indexed_and_preserved_in_viewer_html(result):
    """Superseded invariant: tables used to stay viewer-only (design.md §4.2,
    pre spec 2026-08-12). A row is now indexed as a sentence with a table_id,
    and the <table>/<tr>/<td> markup survives into viewer_html so the row
    still renders as a table row, not a run-on paragraph."""
    by_text = {s.text: s for s in result.sentences}
    row = by_text["Item 7 table text must stay viewer-only"]
    assert row.table_id is not None
    assert "table text must stay viewer-only" in result.canonical_text
    viewer = BeautifulSoup(result.viewer_html, "lxml")
    tr = viewer.find("tr", attrs={"data-sid": str(row.sid)})
    assert tr is not None
    assert tr.find("td") is not None


def test_scripts_and_xbrl_header_are_stripped(result):
    assert "alert(" not in result.viewer_html
    assert "dei:DocumentType" not in result.viewer_html
    assert "10-K" not in result.canonical_text  # ix:hidden content must not leak into text
