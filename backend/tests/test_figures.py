from decimal import Decimal

from api.figures import claim_text, narrow_cells
from api.verify import VerifiedCitation
from pipeline.tables import Cell

SID = 2768
ROW = "Data Center $ 115,186 $ 47,525 $ 15,005"


def cell(index, value, start, kind="number"):
    raw = f"{value:,}"
    return Cell(
        sid=SID, col=index, cell_index=index, char_start=start, char_end=start + len(raw),
        table_id=1, raw=raw, value=Decimal(value), kind=kind, row_label="Data Center",
        column_label=None, scale_applies=True,
    )


CELLS = [
    cell(2, 115186, ROW.index("115,186")),
    cell(6, 47525, ROW.index("47,525")),
    cell(10, 15005, ROW.index("15,005")),
]


def citation(cells=((SID, 2), (SID, 6), (SID, 10)), marker=1, verified=True):
    return VerifiedCitation(
        marker=marker, chunk_id=7, quote=ROW, verified=verified,
        accession="0001045810-25-000023", sids=[SID], ticker="NVDA",
        form_type="10-K", filing_date="2025-02-26", cells=tuple(cells),
    )


def narrowed(prose, **kw):
    return narrow_cells(citation(**kw), CELLS, prose).cells


def test_billions_with_three_decimals_keeps_only_that_year():
    assert narrowed("Data Center revenue was $115.186 billion in fiscal 2025 [1].") == (
        (SID, 2),
    )


def test_billions_rounded_to_one_decimal_keeps_only_that_year():
    assert narrowed("NVIDIA's Data Center revenue was $115.2 billion [1].") == ((SID, 2),)


def test_exact_millions_match():
    assert narrowed("Data Center revenue was $115,186 million [1].") == ((SID, 2),)


def test_two_stated_figures_keep_both():
    prose = "Data Center revenue grew to $115,186 million from $47,525 million [1]."
    assert narrowed(prose) == ((SID, 2), (SID, 6))


def test_no_stated_figure_leaves_cells_unchanged():
    prose = "Data Center revenue grew sharply in fiscal 2025 [1]."
    assert narrowed(prose) == ((SID, 2), (SID, 6), (SID, 10))


def test_single_cell_is_unchanged_even_if_unstated():
    assert narrowed("Revenue rose [1].", cells=((SID, 6),)) == ((SID, 6),)


def test_wrong_rounding_does_not_match():
    # 115,186 in billions to one decimal is 115.2, never 115.1.
    assert narrowed("It was $115.1 billion [1].") == ((SID, 2), (SID, 6), (SID, 10))


def test_verified_sids_and_quote_are_untouched():
    before = citation()
    after = narrow_cells(before, CELLS, "It was $115.2 billion [1].")
    assert after.cells == ((SID, 2),)
    assert (after.verified, after.sids, after.quote, after.accession) == (
        before.verified, before.sids, before.quote, before.accession,
    )


def test_only_the_claim_around_the_marker_counts():
    # [1] cites the whole row but its claim states FY2025 only; the FY2024
    # figure belongs to [2]'s claim.
    prose = "Data Center was $115.2 billion [1], up from $47.5 billion [2]."
    assert narrowed(prose) == ((SID, 2),)
    assert narrowed(prose, marker=2) == ((SID, 6),)


def test_claim_text_handles_adjacent_markers_and_trailing_period():
    prose = "Revenue was $1.0 billion. [1][2] Margin was 46% [3]."
    assert claim_text(prose, 1) == "Revenue was $1.0 billion"
    assert claim_text(prose, 2) == "Revenue was $1.0 billion"
    assert claim_text(prose, 3).strip() == "Margin was 46%"


def test_marker_absent_from_prose_falls_back_to_whole_prose():
    assert narrowed("Data Center was $115.2 billion.") == ((SID, 2),)


# --- markers placed before the claim they cite ---

MARKER_FIRST = (
    "Here is the comparison: [1] NVIDIA's Data Center revenue for fiscal year 2025"
    " (year ended January 26, 2025) was $115,186 million ($115.2 billion)."
    " [2] Microsoft's Microsoft Cloud revenue for fiscal year 2025 was $168.9 billion."
)


def test_marker_first_cites_the_words_after_it():
    assert narrowed(MARKER_FIRST) == ((SID, 2),)


def test_marker_first_does_not_borrow_the_previous_claim():
    # [2] starts the Microsoft sentence; the NVIDIA figures before it are [1]'s.
    assert narrowed(MARKER_FIRST, marker=2) == ((SID, 2), (SID, 6), (SID, 10))


def test_marker_first_at_the_start_of_the_answer():
    assert narrowed("[1] Data Center revenue was $47.5 billion in fiscal 2024.") == (
        (SID, 6),
    )


def test_both_styles_in_one_answer():
    # [1] opens its claim, [2] closes its own; both cite the whole row.
    prose = (
        "[1] Fiscal 2025 Data Center revenue was $115,186 million."
        " In fiscal 2024 it was $47.5 billion [2]."
    )
    assert narrowed(prose) == ((SID, 2),)
    assert narrowed(prose, marker=2) == ((SID, 6),)


def test_period_before_a_closing_marker_still_reads_backwards():
    # "X. [1] Y [2]." -- the words after [1] are closed by [2], so they are not [1]'s.
    prose = "Data Center was $115.2 billion. [1] In fiscal 2024 it was $47.5 billion [2]."
    assert narrowed(prose) == ((SID, 2),)
    assert narrowed(prose, marker=2) == ((SID, 6),)


def test_figure_after_a_mid_sentence_marker_is_the_fallback():
    assert narrowed("Per the 10-K [1], Data Center revenue was $115.2 billion.") == (
        (SID, 2),
    )
