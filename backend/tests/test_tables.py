from decimal import Decimal
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from pipeline.tables import TableInfo, parse_table

FIXTURES = Path(__file__).parent / "fixtures"


def rows_of(html: str) -> list[tuple[int, str, object]]:
    """(sid, sentence text, <tr>) for each row with text, numbered from 0 --
    the same row text the canonicalizer builds."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.find("table").find_all("tr"):
        text = " ".join(tr.get_text(" ", strip=True).split())
        if text:
            out.append((len(out), text, tr))
    return out


def fixture_rows(name: str):
    return rows_of((FIXTURES / name).read_text(encoding="utf-8"))


def by_key(cells):
    return {(c.sid, c.col): c for c in cells}


# --- NVIDIA: colspan alignment, '$' in its own cell --------------------------


@pytest.fixture(scope="module")
def nvda():
    return parse_table(1, fixture_rows("edgar_nvda_revenue.html"), "revenue by market:")


def test_nvda_value_binds_to_its_grid_column_not_its_cell_index(nvda):
    _, cells = nvda
    cell = by_key(cells)[(3, 4)]
    assert cell.raw == "115,186"
    assert cell.value == Decimal("115186")
    assert cell.cell_index == 2
    assert cell.column_label == "Year Ended › Jan 26, 2025"
    assert cell.row_label == "Data Center"
    assert cell.kind == "number"


def test_nvda_row_without_dollar_cells_binds_to_the_same_columns(nvda):
    _, cells = nvda
    compute = [c for c in cells if c.sid == 4]
    assert [(c.raw, c.column_label) for c in compute] == [
        ("102,196", "Year Ended › Jan 26, 2025"),
        ("38,950", "Year Ended › Jan 28, 2024"),
        ("11,317", "Year Ended › Jan 29, 2023"),
    ]


def test_nvda_scale_comes_from_the_header_band_and_is_stripped_from_labels(nvda):
    info, cells = nvda
    assert info == TableInfo(1, "revenue by market:", Decimal("1000000"), True)
    assert all("millions" not in (c.column_label or "").lower() for c in cells)
    assert all(c.scale_applies for c in cells)


def test_nvda_char_spans_slice_the_row_sentence(nvda):
    _, cells = nvda
    rows = {sid: text for sid, text, _ in fixture_rows("edgar_nvda_revenue.html")}
    for cell in cells:
        assert rows[cell.sid][cell.char_start : cell.char_end] == cell.raw


# --- Apple: a second header band mid-table, scale only in the caption --------


@pytest.fixture(scope="module")
def aapl():
    return parse_table(1, fixture_rows("edgar_aapl_segments.html"), "Segments (in millions):")


def test_aapl_second_band_replaces_the_first(aapl):
    _, cells = aapl
    first = by_key(cells)[(2, 4)]
    second = by_key(cells)[(8, 4)]
    assert (first.raw, first.column_label) == (
        "45,093",
        "Three Months Ended March 28, 2026 › Americas",
    )
    assert (second.raw, second.column_label) == (
        "40,315",
        "Three Months Ended March 29, 2025 › Americas",
    )


def test_aapl_parentheses_mean_negative(aapl):
    _, cells = aapl
    cell = by_key(cells)[(3, 3)]
    assert cell.raw == "( 23,114 )"
    assert cell.value == Decimal("-23114")


def test_aapl_dash_is_a_nil_cell(aapl):
    _, cells = aapl
    cell = by_key(cells)[(2, 34)]
    assert cell.kind == "nil"
    assert cell.value is None
    assert cell.column_label == "Three Months Ended March 28, 2026 › Corporate"


def test_aapl_scale_falls_back_to_the_caption(aapl):
    info, _ = aapl
    assert info.scale == Decimal("1000000")
    assert info.splittable is True


def test_aapl_without_a_caption_scale_is_unknown():
    info, _ = parse_table(1, fixture_rows("edgar_aapl_segments.html"), None)
    assert info.scale is None


# --- Microsoft: year headers, group rows, percent column ---------------------


@pytest.fixture(scope="module")
def msft():
    return parse_table(1, fixture_rows("edgar_msft_segments.html"), "SEGMENT RESULTS OF OPERATIONS")


def test_msft_year_header_row_is_a_header_not_data(msft):
    _, cells = msft
    assert 0 not in {c.sid for c in cells}
    cell = by_key(cells)[(6, 7)]
    assert (cell.raw, cell.column_label) == ("106,265", "2025")


def test_msft_repeated_row_label_is_qualified_by_its_group(msft):
    _, cells = msft
    assert by_key(cells)[(2, 3)].row_label == "Productivity and Business Processes › Revenue"
    assert by_key(cells)[(6, 3)].row_label == "Intelligent Cloud › Revenue"


def test_msft_percent_cell_is_never_scaled(msft):
    info, cells = msft
    cell = by_key(cells)[(6, 11)]
    assert (cell.raw, cell.kind, cell.value) == ("30%", "percent", Decimal("30"))
    assert cell.column_label == "Percentage Change"
    assert cell.scale_applies is False
    assert info.scale == Decimal("1000000")


# --- NULL over guess, and cell-shape edge cases -------------------------------


def test_a_number_under_two_header_cells_gets_no_column_label():
    rows = rows_of(
        "<table><tr><td></td><td>Q1</td><td>Q2</td></tr>"
        '<tr><td>Sales</td><td colspan="2">1,000</td></tr></table>'
    )
    _, cells = parse_table(1, rows, None)
    assert cells[0].column_label is None


def test_a_table_with_no_header_band_is_not_splittable():
    rows = rows_of("<table><tr><td>Sales</td><td>1,000</td></tr></table>")
    info, cells = parse_table(1, rows, None)
    assert info.splittable is False
    assert cells[0].column_label is None
    assert cells[0].value == Decimal("1000")


def test_lone_percent_and_paren_cells_attach_to_the_number_on_their_left():
    rows = rows_of(
        "<table><tr><td></td><td>2025</td><td></td><td>Change</td><td></td></tr>"
        "<tr><td>Margin</td><td>(1,234</td><td>)</td><td>16</td><td>%</td></tr></table>"
    )
    _, cells = parse_table(1, rows, None)
    assert [(c.raw, c.value, c.kind) for c in cells] == [
        ("(1,234", Decimal("-1234"), "number"),
        ("16", Decimal("16"), "percent"),
    ]


def test_per_share_rows_are_exempt_when_the_scale_says_so():
    rows = rows_of(
        "<table><tr><td>(In millions, except per share amounts)</td><td>2025</td></tr>"
        "<tr><td>Net income</td><td>1,000</td></tr>"
        "<tr><td>Diluted earnings per share</td><td>1.65</td></tr></table>"
    )
    _, cells = parse_table(1, rows, None)
    assert [(c.row_label, c.scale_applies) for c in cells] == [
        ("Net income", True),
        ("Diluted earnings per share", False),
    ]


def test_th_headers_nbsp_and_a_bad_colspan_are_handled():
    rows = rows_of(
        '<table><tr><th></th><th colspan="bogus">2025</th></tr>'
        "<tr><td>Revenue</td><td>$&nbsp;1,234</td></tr></table>"
    )
    _, cells = parse_table(1, rows, None)
    assert (cells[0].raw, cells[0].value, cells[0].column_label) == (
        "$ 1,234",
        Decimal("1234"),
        "2025",
    )
    assert cells[0].col == 1


def test_char_spans_are_null_when_the_row_text_does_not_reproduce():
    sid, _text, tr = rows_of(
        "<table><tr><td></td><td>2025</td></tr><tr><td>Revenue</td><td>1,234</td></tr></table>"
    )[1]
    header = rows_of("<table><tr><td></td><td>2025</td></tr></table>")[0]
    _, cells = parse_table(1, [header, (sid, "not the row text", tr)], None)
    assert (cells[0].char_start, cells[0].char_end) == (None, None)
    assert cells[0].value == Decimal("1234")
