from decimal import Decimal
from pathlib import Path

import pytest

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
    assert pieces[0].text.startswith(
        "The following table summarizes revenue by specialized markets: Year Ended Jan 26, 2025"
    )
    assert "Table:" not in pieces[0].context
    assert all(
        p.context.startswith("Table: The following table summarizes") for p in pieces[1:]
    )
    assert "Data Center $ 115,186" in pieces[0].text
    assert all("Columns: Year Ended › [Jan 26, 2025" in p.context for p in pieces)
    assert MAX_TOKENS == 450


CAPTION = "The following table summarizes segment results:"


def lead_in_case(max_tokens=None, lead_text=CAPTION):
    """prose, prose, lead-in, then a small table that cannot join the prose."""
    rows, cells = table(rows_per_band=2, bands=1, start=3)
    sentences = [prose(0), prose(1), prose(2, lead_text), *rows]
    tables = {1: TableInfo(1, CAPTION, None, True)}
    if max_tokens is None:
        unit = sum(count_tokens(s.text) for s in [sentences[2], *rows])
        # room for the lead-in + table + context, but not for the prose before it
        max_tokens = unit + count_tokens("Columns: Three Months Ended period 0") + 5
    return sentences, cells, tables, max_tokens


def split_lead_in_case(lead_text=CAPTION):
    """prose, lead-in, then an over-budget splittable table."""
    rows, cells = table(rows_per_band=8, bands=2, start=2)
    sentences = [prose(0), prose(1, lead_text), *rows]
    tables = {1: TableInfo(1, CAPTION, None, True)}
    return sentences, cells, tables


def test_a_split_tables_lead_in_sentence_moves_into_its_first_piece():
    sentences, cells, tables = split_lead_in_case()
    chunks = chunk_sentences(sentences, max_tokens=150, tables=tables, cells=cells)
    assert (chunks[0].sid_start, chunks[0].sid_end, chunks[0].table_id) == (0, 0, None)
    assert chunks[1].sid_start == 1
    assert chunks[1].text.startswith(CAPTION)
    assert all(c.table_id == 1 for c in chunks[1:])
    assert covered(chunks) == [s.sid for s in sentences]


def test_context_drops_the_caption_when_the_lead_in_is_in_the_chunk():
    rows, cells = table(rows_per_band=2, bands=1, start=1)
    sentences = [prose(0, CAPTION), *rows]
    tables = {1: TableInfo(1, CAPTION, None, True)}
    chunks = chunk_sentences(sentences, tables=tables, cells=cells)
    assert len(chunks) == 1
    assert chunks[0].context == "Columns: Three Months Ended period 0"


def test_a_sentence_that_is_not_the_caption_is_not_moved():
    sentences, cells, tables = split_lead_in_case(lead_text="Something else entirely.")
    chunks = chunk_sentences(sentences, max_tokens=150, tables=tables, cells=cells)
    assert (chunks[0].sid_start, chunks[0].sid_end, chunks[0].table_id) == (0, 1, None)
    assert chunks[1].sid_start == 2
    assert chunks[1].context.startswith("Table: The following table summarizes")


def test_a_fitting_table_continues_the_current_chunk_like_before():
    rows, cells = table(rows_per_band=3, bands=1, start=2)
    filler = "Net sales grew on higher demand across every region and product line this year."
    sentences = [prose(0, filler), prose(1, filler), *rows]
    tables = {1: TableInfo(1, "Small", None, True)}
    context = "Table: Small | Columns: Three Months Ended period 0"
    lead = sum(count_tokens(s.text) for s in sentences[:2])
    whole = sum(count_tokens(s.text) for s in rows)
    budget = whole + count_tokens(context) + 2  # the table fits the budget on its own
    assert lead + count_tokens(rows[0].text) + count_tokens(rows[1].text) <= budget
    assert lead + whole > budget  # ...but prose plus the whole table does not
    chunks = chunk_sentences(sentences, max_tokens=budget, tables=tables, cells=cells)
    legacy = chunk_sentences(sentences, max_tokens=budget)
    assert [(c.sid_start, c.sid_end) for c in chunks] == [(c.sid_start, c.sid_end) for c in legacy]
    assert chunks[0].sid_end == rows[-1].sid  # the table runs on past the budget
    assert chunks[0].context == context


def test_prose_after_a_table_joins_the_same_chunk_when_it_fits():
    rows, cells = table(rows_per_band=2, bands=1, start=1)
    sentences = [prose(0), *rows, prose(4), prose(5)]
    tables = {1: TableInfo(1, "T", None, True)}
    chunks = chunk_sentences(sentences, tables=tables, cells=cells)
    assert [(c.sid_start, c.sid_end) for c in chunks] == [(0, 5)]


FIXTURE_NAMES = [
    "edgar_nvda_revenue.html",
    "edgar_aapl_segments.html",
    "edgar_msft_segments.html",
    "edgar_jpm_highlights.html",
    "mini_10k.html",
    "table_shapes.html",
]


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_boundaries_match_the_legacy_chunker_when_no_table_is_split(name):
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    canonical = canonicalize(raw, "10-K")
    tables = {t.table_id: t for t in canonical.tables}
    with_tables = chunk_sentences(canonical.sentences, tables=tables, cells=canonical.cells)
    legacy = chunk_sentences(canonical.sentences)
    assert [(c.sid_start, c.sid_end) for c in with_tables] == [
        (c.sid_start, c.sid_end) for c in legacy
    ]


def test_prose_after_a_split_table_chunks_like_legacy_when_the_lead_in_was_alone():
    rows, cells = table(rows_per_band=8, bands=2, start=1)
    tail = " ".join(["Net sales grew on higher demand this year."] * 8)
    last = rows[-1].sid
    sentences = [prose(0, CAPTION), *rows, prose(last + 1, tail), prose(last + 2, tail)]
    tables = {1: TableInfo(1, CAPTION, None, True)}
    chunks = chunk_sentences(sentences, max_tokens=150, tables=tables, cells=cells)
    trailing = [s for s in sentences if s.table_id is None and s.sid > 0]
    legacy = chunk_sentences(trailing, max_tokens=150)
    assert [(c.sid_start, c.sid_end) for c in chunks if c.table_id is None] == [
        (c.sid_start, c.sid_end) for c in legacy
    ]
    assert len(legacy) == 1


def test_a_split_table_keeps_its_lead_in_in_the_first_piece():
    rows, cells = table(rows_per_band=8, bands=2, start=1)
    sentences = [prose(0, CAPTION), *rows]
    tables = {1: TableInfo(1, CAPTION, None, True)}
    chunks = chunk_sentences(sentences, max_tokens=150, tables=tables, cells=cells)
    data_sids = {c.sid for c in cells}
    assert len(chunks) >= 2
    assert chunks[0].sid_start == 0
    assert chunks[0].text.startswith(CAPTION)
    assert all(c.table_id == 1 for c in chunks)
    assert "Table:" not in chunks[0].context
    assert all(c.context.startswith(f"Table: {CAPTION}") for c in chunks[1:])
    assert all(c.sid_end in data_sids for c in chunks)
    assert covered(chunks) == [s.sid for s in sentences]
