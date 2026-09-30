from decimal import Decimal

from pipeline.tables import Cell, TableInfo, render_context, row_contexts


def cell(sid, col, column_label, row_label="Revenue", table_id=1):
    return Cell(
        sid=sid,
        col=col,
        cell_index=col,
        char_start=None,
        char_end=None,
        table_id=table_id,
        raw="1",
        value=Decimal("1"),
        kind="number",
        row_label=row_label,
        column_label=column_label,
        scale_applies=True,
    )


NVDA = TableInfo(
    1, "The following table summarizes revenue by specialized markets:", Decimal("1E+6"), True
)


def test_context_names_caption_scale_and_factored_columns():
    row = [
        cell(6, 16, "Year Ended › Jan 29, 2023", "Data Center"),
        cell(6, 4, "Year Ended › Jan 26, 2025", "Data Center"),
        cell(6, 10, "Year Ended › Jan 28, 2024", "Data Center"),
    ]
    assert render_context(NVDA, row) == (
        "Table: The following table summarizes revenue by specialized markets:"
        " | Scale: in millions"
        " | Columns: Year Ended › [Jan 26, 2025; Jan 28, 2024; Jan 29, 2023]"
    )


def test_context_adds_the_group_and_skips_unknowns():
    info = TableInfo(1, None, None, True)
    row = [
        cell(9, 3, "2026", "Intelligent Cloud › Revenue"),
        cell(9, 7, "2025", "Intelligent Cloud › Revenue"),
        cell(9, 11, None, "Intelligent Cloud › Revenue"),
    ]
    assert render_context(info, row) == "Columns: 2026; 2025 | Group: Intelligent Cloud"


def test_columns_with_no_shared_prefix_are_listed_plainly():
    info = TableInfo(1, None, None, True)
    row = [cell(1, 1, "2026"), cell(1, 2, "Percentage Change")]
    assert render_context(info, row) == "Columns: 2026; Percentage Change"


def test_a_long_caption_is_truncated():
    info = TableInfo(1, "x" * 500, None, True)
    assert render_context(info, [cell(1, 1, None)]) == "Table: " + "x" * 200


def test_row_contexts_cover_data_rows_only_and_skip_unknown_tables():
    tables = {1: TableInfo(1, None, None, True)}
    cells = [cell(5, 1, "2025"), cell(5, 2, "2024"), cell(7, 1, "2025", table_id=2)]
    assert row_contexts(tables, cells) == {5: "Columns: 2025; 2024"}
