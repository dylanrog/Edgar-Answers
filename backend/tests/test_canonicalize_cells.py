from pathlib import Path

import pytest

from pipeline.canonicalize import canonicalize

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return canonicalize((FIXTURES / name).read_text(encoding="utf-8"), "10-K")


@pytest.fixture(scope="module")
def nvda():
    return load("edgar_nvda_revenue.html")


def test_cells_carry_the_real_sentence_ids(nvda):
    by_sid = {s.sid: s.text for s in nvda.sentences}
    cell = next(c for c in nvda.cells if c.raw == "115,186")
    assert by_sid[cell.sid] == "Data Center $ 115,186 $ 47,525 $ 15,005"
    assert (cell.col, cell.cell_index) == (4, 2)
    assert cell.column_label == "Year Ended › Jan 26, 2025"


def test_the_caption_is_the_preceding_prose_sentence(nvda):
    assert [t.caption for t in nvda.tables] == [
        "The following table summarizes revenue by specialized markets:"
    ]


def test_header_rows_produce_no_cells(nvda):
    headers = {
        "Year Ended",
        "Jan 26, 2025 Jan 28, 2024 Jan 29, 2023",
        "Revenue by End Market: (In millions)",
    }
    header_sids = {s.sid for s in nvda.sentences if s.text in headers}
    assert len(header_sids) == 3
    assert header_sids.isdisjoint({c.sid for c in nvda.cells})


def test_a_table_right_after_another_has_no_caption():
    shapes = load("table_shapes.html")
    assert [t.caption for t in shapes.tables] == [
        "Net sales discussion follows.",
        None,
        "Loose text beside a table.",
        None,
    ]


@pytest.mark.parametrize(
    "name", ["edgar_nvda_revenue.html", "edgar_aapl_segments.html", "edgar_msft_segments.html"]
)
def test_every_cell_span_slices_its_row_sentence(name):
    canonical = load(name)
    by_sid = {s.sid: s.text for s in canonical.sentences}
    assert canonical.cells
    for cell in canonical.cells:
        assert by_sid[cell.sid][cell.char_start : cell.char_end] == cell.raw


def test_prose_only_filings_have_no_tables():
    canonical = canonicalize("<html><body><p>Only prose here.</p></body></html>", "10-K")
    assert canonical.tables == [] and canonical.cells == []
