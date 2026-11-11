import dataclasses
from decimal import Decimal

from api.figures import claim_sides, narrow_cells
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


def test_a_claim_with_no_figure_marks_no_cells():
    # "fiscal 2025" is a year, not a figure: the claim states none, so no
    # cell is emphasized and the row is highlighted whole.
    prose = "Data Center revenue grew sharply in fiscal 2025 [1]."
    assert narrowed(prose) == ()


def test_a_claim_with_an_unmatched_figure_leaves_cells_unchanged():
    # A figure is stated but it is none of the cited ones: fail closed.
    prose = "Data Center revenue grew 142% [1]."
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


def test_claim_sides_handles_adjacent_markers_and_trailing_period():
    prose = "Revenue was $1.0 billion. [1][2] Margin was 46% [3]."
    # [1][2] share a group; the words after it are closed by [3], so not theirs.
    assert claim_sides(prose, 1) == ("Revenue was $1.0 billion", "")
    assert claim_sides(prose, 2) == ("Revenue was $1.0 billion", "")
    before, after = claim_sides(prose, 3)
    assert (before.strip(), after) == ("Margin was 46%", "")


def test_marker_absent_from_prose_leaves_cells_unchanged():
    # Fail closed: with no marker there is no claim to read, so nothing narrows.
    assert claim_sides("Data Center was $115.2 billion.", 1) is None
    assert narrowed("Data Center was $115.2 billion.") == ((SID, 2), (SID, 6), (SID, 10))


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


# --- both sides read: an ambiguous claim keeps both candidates ---


def test_ambiguous_period_before_marker_keeps_both_candidates():
    # "X. [1] Y. [2]" reads as "[1] closes X" or "[1] opens Y". Picking one
    # side marked FY2024 for [1]; the union keeps FY2025 too.
    prose = "FY2025 Data Center was $115.2B. [1] FY2024 was $47.5B. [2]"
    assert narrowed(prose) == ((SID, 2), (SID, 6))


# --- units ---


def test_zero_decimal_billions_beside_an_exact_figure():
    prose = "Revenue was about $115 billion, up from $47,525 million [1]."
    assert narrowed(prose) == ((SID, 2), (SID, 6))


def test_a_percent_never_matches_a_number_cell():
    # 47.5% is not $47,525 million.
    prose = "Data Center revenue of roughly $115 billion carried a 47.5% share [1]."
    assert narrowed(prose) == ((SID, 2),)


def test_a_bare_decimal_without_a_unit_matches_nothing_scaled():
    assert narrowed("Data Center grew to 115.2 [1].") == ((SID, 2), (SID, 6), (SID, 10))


def test_bn_and_b_abbreviations():
    assert narrowed("Data Center revenue was 115.2bn [1].") == ((SID, 2),)
    assert narrowed("Data Center revenue was $47.5B [1].") == ((SID, 6),)


def test_thousands_scaled_table_reads_million_as_one_unit_up():
    # A thousands table's 115,186 is $115.2 million.
    assert narrowed("Revenue was $115.2 million [1].") == ((SID, 2),)


def test_near_miss_fails_closed():
    # $115.0 billion is within rounding of 115,186 but not its rounding, while
    # $47,525 million matches exactly: rather than mark only FY2024 beside a
    # claim that is likely about FY2025 as well, keep every cited cell.
    prose = "Revenue was $115.0 billion, up from $47,525 million [1]."
    assert narrowed(prose) == ((SID, 2), (SID, 6), (SID, 10))


def _cells(*specs, sid=SID):
    return [
        Cell(
            sid=sid, col=i, cell_index=i, char_start=None, char_end=None, table_id=1,
            raw=str(v), value=Decimal(v), kind=kind, row_label="x", column_label=None,
            scale_applies=True,
        )
        for i, v, kind in specs
    ]


def test_parenthesised_negative_figures():
    cells = _cells((2, "-1234", "number"), (6, "567", "number"))
    c = citation(cells=((SID, 2), (SID, 6)))
    assert narrow_cells(c, cells, "Other income was $(1,234) million [1].").cells == ((SID, 2),)
    assert narrow_cells(c, cells, "A loss of $1.2 billion [1].").cells == ((SID, 2),)


def test_percent_cells_need_a_percent_figure():
    cells = _cells((2, "75.0", "percent"), (6, "72.7", "percent"))
    c = citation(cells=((SID, 2), (SID, 6)))
    assert narrow_cells(c, cells, "Gross margin was 75% [1].").cells == ((SID, 2),)
    assert narrow_cells(c, cells, "Gross margin was 72.7% [1].").cells == ((SID, 6),)
    assert narrow_cells(c, cells, "Gross margin was 72.7 points [1].").cells == (
        (SID, 2), (SID, 6),
    )


# --- quotes spanning rows ---


def test_a_row_with_no_stated_figure_loses_its_cells():
    # Data Center states FY2025; the Compute row states nothing, so it drops
    # its cell emphasis and the viewer highlights it as a plain row -- marking
    # its figures would point at numbers the answer never gives.
    compute = _cells((2, "102196", "number"), (6, "38950", "number"), sid=SID + 1)
    cells = CELLS + compute
    c = citation(cells=((SID, 2), (SID, 6), (SID, 10), (SID + 1, 2), (SID + 1, 6)))
    narrowed_c = narrow_cells(c, cells, "Data Center revenue was $115.2 billion [1].")
    assert narrowed_c.cells == ((SID, 2),)
    assert narrowed_c.sids == c.sids  # both rows stay cited


# The live tutorial case: turn 1 adds a true third claim quoting the Data
# Center, Compute and Networking rows (sids 2768-2770).
DC, COMPUTE, NETWORKING = 2768, 2769, 2770
SEGMENT_CELLS = CELLS + _cells((1, "102196", "number"), sid=COMPUTE) + _cells(
    (1, "12990", "number"), sid=NETWORKING
)
SEGMENT_CITED = ((DC, 2), (DC, 6), (DC, 10), (COMPUTE, 1), (NETWORKING, 1))
TUTORIAL_ANSWER = (
    "NVIDIA's Data Center revenue was $115.2 billion in fiscal 2025 [1], while"
    " Microsoft Cloud revenue was $168.9 billion in fiscal 2025 [2]. [3] NVIDIA's"
    " Data Center segment includes both Compute ($102,196 million) and Networking"
    " ($12,990 million)."
)


def test_segment_breakdown_marks_only_compute_and_networking():
    c = dataclasses.replace(citation(cells=SEGMENT_CITED, marker=3), sids=[DC, COMPUTE, NETWORKING])
    kept = narrow_cells(c, SEGMENT_CELLS, TUTORIAL_ANSWER)
    assert kept.cells == ((COMPUTE, 1), (NETWORKING, 1))
    assert kept.sids == [DC, COMPUTE, NETWORKING]


def test_tutorial_first_claim_still_marks_fiscal_2025_only():
    assert narrow_cells(citation(), CELLS, TUTORIAL_ANSWER).cells == ((DC, 2),)


# --- rounded or truncated figures near a cell fail closed ---


def test_billions_table_rounded_figure_fails_closed():
    # A billions-scale table (JPM): "about $22 billion" is 22.1 rounded, not a
    # miss, so marking only 23.4 would hide the figure the answer gives first.
    cells = _cells((2, "22.1", "number"), (6, "23.4", "number"))
    c = citation(cells=((SID, 2), (SID, 6)))
    prose = "Cash was about $22 billion, down from $23.4 billion [1]."
    assert narrow_cells(c, cells, prose).cells == ((SID, 2), (SID, 6))


def test_unitless_per_share_rounded_figure_fails_closed():
    cells = _cells((2, "6.08", "number"), (6, "6.13", "number"), (10, "5.67", "number"))
    c = citation(cells=((SID, 2), (SID, 6), (SID, 10)))
    prose = "Diluted EPS was about $6.1, versus $6.13 a year earlier [1]."
    assert narrow_cells(c, cells, prose).cells == ((SID, 2), (SID, 6), (SID, 10))


def test_truncated_billions_figure_fails_closed():
    # "$47 billion" truncates 47,525 (47.5 would round); a whole unit of slack.
    prose = "Revenue was $115,186 million, up from about $47 billion [1]."
    assert narrowed(prose) == ((SID, 2), (SID, 6), (SID, 10))


# --- unit spellings ---


def test_lowercase_b_is_billion():
    prose = "Data Center was $115.2b, up from $47,525 million [1]."
    assert narrowed(prose) == ((SID, 2), (SID, 6))


def test_hyphenated_unit():
    prose = "The $115.2-billion Data Center haul beat $47.5 billion [1]."
    assert narrowed(prose) == ((SID, 2), (SID, 6))


def test_range_ends_share_the_unit():
    prose = "Revenue of $115-116 billion, up from $47.5 billion [1]."
    assert narrowed(prose) == ((SID, 2), (SID, 6))


def test_trillion_is_read_and_never_hides_the_stated_cell():
    from api.figures import is_stated, stated_figures

    assert is_stated(CELLS[0], stated_figures("$0.1152 trillion"))
    # At this scale every cell is within half a unit, so it fails closed
    # rather than marking FY2024 alone.
    prose = "Data Center was $0.1152 trillion, up from $47.5 billion [1]."
    assert narrowed(prose) == ((SID, 2), (SID, 6), (SID, 10))


# --- bare years are not figures ---

YEAR_CELLS = CELLS + _cells(
    (2, "102196", "number"), (6, "2025", "number"), (10, "11000", "number"), sid=SID + 1
)
YEAR_CITED = tuple((c.sid, c.cell_index) for c in YEAR_CELLS)


def test_a_bare_year_does_not_mark_a_coinciding_cell():
    c = citation(cells=YEAR_CITED)
    prose = "In fiscal 2025 Data Center revenue was $115.2 billion [1]."
    assert narrow_cells(c, YEAR_CELLS, prose).cells == ((SID, 2),)


def test_a_bare_year_alone_is_not_the_only_mark():
    # The claim states no figure (the year doesn't count), so nothing is
    # marked -- in particular not (and not scrolled to) the 2,025 cell.
    c = citation(cells=YEAR_CITED)
    prose = "In fiscal 2025 Data Center revenue was over one hundred billion dollars [1]."
    assert narrow_cells(c, YEAR_CELLS, prose).cells == ()


def test_a_year_sized_figure_with_a_dollar_sign_or_comma_still_counts():
    c = citation(cells=YEAR_CITED)
    assert narrow_cells(c, YEAR_CELLS, "Other was $2025 [1].").cells == ((SID + 1, 6),)
    assert narrow_cells(c, YEAR_CELLS, "Other was 2,025 [1].").cells == ((SID + 1, 6),)


# --- a qualitative claim (live 30-run vetting, turn 1) ---

QUALITATIVE_ANSWER = (
    "NVIDIA's Data Center revenue was $115.2 billion in fiscal 2025 [1], while"
    " Microsoft Cloud revenue was $168.9 billion in fiscal 2025 [2]. However, it's"
    " important to note that these are different metrics: [3] NVIDIA's Data Center"
    " segment includes both computing and networking products, while [4] Microsoft"
    " Cloud spans several businesses."
)


def test_a_qualitative_claim_marks_no_cells():
    c = dataclasses.replace(
        citation(cells=SEGMENT_CITED, marker=3), sids=[DC, COMPUTE, NETWORKING]
    )
    kept = narrow_cells(c, SEGMENT_CELLS, QUALITATIVE_ANSWER)
    assert kept.cells == ()
    assert kept.sids == [DC, COMPUTE, NETWORKING]
    assert (kept.verified, kept.quote) == (c.verified, c.quote)


def test_the_figure_claims_beside_it_are_unaffected():
    assert narrow_cells(citation(), CELLS, QUALITATIVE_ANSWER).cells == ((DC, 2),)
    c = dataclasses.replace(
        citation(cells=SEGMENT_CITED, marker=3), sids=[DC, COMPUTE, NETWORKING]
    )
    assert narrow_cells(c, SEGMENT_CELLS, TUTORIAL_ANSWER).cells == (
        (COMPUTE, 1), (NETWORKING, 1),
    )
