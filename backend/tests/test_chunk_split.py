from decimal import Decimal
from pathlib import Path

from pipeline.canonicalize import Sentence, canonicalize
from pipeline.chunk import MAX_TOKENS, chunk_sentences, count_tokens, embed_input
from pipeline.tables import Cell, TableInfo

FIXTURES = Path(__file__).parent / "fixtures"


def prose(sid, text="Some prose sentence about the business."):
    return Sentence(sid, "item7", text, 0, len(text), None)


def row(sid, text, table_id=1):
    return Sentence(sid, "item7", text, 0, len(text), table_id)


def data_cell(sid, table_id=1, label="2025"):
    return Cell(
        sid, 1, 1, None, None, table_id, "1", Decimal("1"), "number", "Revenue", label, True
    )


def table(rows_per_band=8, bands=2, table_id=1, start=1):
    """A header row then `rows_per_band` data rows, repeated `bands` times."""
    sentences, cells, sid = [], [], start
    for band in range(bands):
        sentences.append(row(sid, f"Three Months Ended period {band}", table_id))
        sid += 1
        for i in range(rows_per_band):
            sentences.append(
                row(sid, f"Line item {band}-{i} $ 12,345 $ 67,890 $ 11,121 $ 31,415", table_id)
            )
            cells.append(data_cell(sid, table_id, label=f"Three Months Ended period {band}"))
            sid += 1
    return sentences, cells


def covered(chunks):
    return [sid for c in chunks for sid in range(c.sid_start, c.sid_end + 1)]


def test_an_over_budget_splittable_table_is_split_into_tagged_pieces():
    rows, cells = table(rows_per_band=8, bands=2)
    sentences = [prose(0), *rows, prose(rows[-1].sid + 1)]
    tables = {1: TableInfo(1, "Segment results", Decimal("1E+6"), True)}
    chunks = chunk_sentences(sentences, max_tokens=150, tables=tables, cells=cells)
    pieces = [c for c in chunks if c.table_id == 1]
    assert len(pieces) >= 2
    assert covered(chunks) == [s.sid for s in sentences]
    for piece in pieces:
        assert piece.context.startswith("Table: Segment results | Scale: in millions")
        assert piece.token_count + count_tokens(piece.context) <= 150


def test_pieces_are_isolated_from_surrounding_prose():
    rows, cells = table(rows_per_band=8, bands=2)
    sentences = [prose(0), *rows, prose(rows[-1].sid + 1)]
    tables = {1: TableInfo(1, None, None, True)}
    chunks = chunk_sentences(sentences, max_tokens=150, tables=tables, cells=cells)
    assert (chunks[0].sid_start, chunks[0].sid_end, chunks[0].table_id) == (0, 0, None)
    assert (chunks[-1].sid_start, chunks[-1].table_id) == (rows[-1].sid + 1, None)


def test_a_piece_prefers_to_start_at_a_new_header_band():
    rows, cells = table(rows_per_band=3, bands=2)
    tables = {1: TableInfo(1, None, None, True)}
    budget = sum(count_tokens(s.text) for s in rows[:4]) + 30
    chunks = chunk_sentences(rows, max_tokens=budget, tables=tables, cells=cells)
    assert [(c.sid_start, c.sid_end) for c in chunks] == [(1, 4), (5, 8)]


def test_no_piece_ends_with_header_rows_stranded_from_their_data():
    rows, cells = table(rows_per_band=6, bands=3)
    data_sids = {c.sid for c in cells}
    tables = {1: TableInfo(1, None, None, True)}
    chunks = chunk_sentences(rows, max_tokens=90, tables=tables, cells=cells)
    for chunk in chunks:
        assert chunk.sid_end in data_sids


def test_an_over_budget_table_without_a_parsed_header_stays_whole():
    rows, cells = table(rows_per_band=8, bands=2)
    tables = {1: TableInfo(1, None, None, False)}
    chunks = chunk_sentences(rows, max_tokens=150, tables=tables, cells=cells)
    assert len(chunks) == 1
    assert chunks[0].table_id is None
    assert chunks[0].context == "Columns: Three Months Ended period 0"


def test_a_table_that_fits_joins_the_prose_around_it_and_carries_context():
    rows, cells = table(rows_per_band=2, bands=1)
    sentences = [prose(0), *rows]
    tables = {1: TableInfo(1, "Small table", None, True)}
    chunks = chunk_sentences(sentences, tables=tables, cells=cells)
    assert len(chunks) == 1
    assert chunks[0].table_id is None
    assert chunks[0].context == "Table: Small table | Columns: Three Months Ended period 0"


def test_a_chunk_with_two_tables_carries_one_context_line_per_table():
    first, first_cells = table(rows_per_band=1, bands=1, table_id=1, start=0)
    second, second_cells = table(rows_per_band=1, bands=1, table_id=2, start=2)
    tables = {1: TableInfo(1, "First", None, True), 2: TableInfo(2, "Second", None, True)}
    chunks = chunk_sentences(
        [*first, *second], tables=tables, cells=[*first_cells, *second_cells]
    )
    assert len(chunks) == 1
    assert chunks[0].context.splitlines() == [
        "Table: First | Columns: Three Months Ended period 0",
        "Table: Second | Columns: Three Months Ended period 0",
    ]


def test_prose_chunks_have_no_context_and_embed_their_text_alone():
    chunks = chunk_sentences([prose(0), prose(1)])
    assert chunks[0].context == ""
    assert embed_input(chunks[0]) == chunks[0].text


def test_embed_input_puts_context_first():
    rows, cells = table(rows_per_band=1, bands=1)
    tables = {1: TableInfo(1, "T", None, True)}
    chunk = chunk_sentences(rows, tables=tables, cells=cells)[0]
    assert embed_input(chunk) == f"{chunk.context}\n{chunk.text}"


def test_chunk_text_is_still_the_space_join_of_its_sentences():
    rows, cells = table(rows_per_band=8, bands=2)
    tables = {1: TableInfo(1, "T", None, True)}
    by_sid = {s.sid: s.text for s in rows}
    for chunk in chunk_sentences(rows, max_tokens=150, tables=tables, cells=cells):
        expected = " ".join(by_sid[sid] for sid in range(chunk.sid_start, chunk.sid_end + 1))
        assert chunk.text == expected


def test_real_nvidia_table_splits_with_its_header_in_the_first_piece():
    raw = (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8")
    canonical = canonicalize(raw, "10-K")
    tables = {t.table_id: t for t in canonical.tables}
    chunks = chunk_sentences(
        canonical.sentences, max_tokens=70, tables=tables, cells=canonical.cells
    )
    pieces = [c for c in chunks if c.table_id is not None]
    assert pieces[0].text.startswith("Year Ended Jan 26, 2025")
    assert "Data Center $ 115,186" in pieces[0].text
    assert all("Columns: Year Ended › [Jan 26, 2025" in p.context for p in pieces)
    assert MAX_TOKENS == 450
