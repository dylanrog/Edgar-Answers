"""The repair gate (design §6.3 step 5): when may a second quote turn a failed
citation green? Every doubt fails closed. The NVIDIA rows below are the real
corpus values for accession 0001045810-25-000023 (FY2025 10-K), sids
2768-2770: a millions table whose column labels read "Year Ended › Jan 26,
2025" and so on."""
from decimal import Decimal

from api.figures import claim_years, quote_states
from pipeline.tables import Cell

MILLIONS = {1: Decimal(1000000)}
FY25, FY24, FY23 = (
    "Year Ended › Jan 26, 2025",
    "Year Ended › Jan 28, 2024",
    "Year Ended › Jan 29, 2023",
)


def cell(sid, index, value, *, label=FY25, kind="number", raw=None, scale_applies=True,
         row_label=None):
    return Cell(
        sid=sid, col=index, cell_index=index, char_start=None, char_end=None, table_id=1,
        raw=raw if raw is not None else f"{value:,}", value=Decimal(str(value)), kind=kind,
        row_label=row_label, column_label=label, scale_applies=scale_applies,
    )


DATA_CENTER_ROW = "Data Center $ 115,186 $ 47,525 $ 15,005"
DATA_CENTER = [
    cell(2768, 2, 115186, label=FY25),
    cell(2768, 6, 47525, label=FY24),
    cell(2768, 10, 15005, label=FY23),
]
SEGMENT_ROWS = "Compute 102,196 38,950 11,317 Networking 12,990 8,575 3,688"
SEGMENTS = [
    cell(2769, 1, 102196, label=FY25), cell(2769, 4, 38950, label=FY24),
    cell(2769, 7, 11317, label=FY23), cell(2770, 1, 12990, label=FY25),
    cell(2770, 4, 8575, label=FY24), cell(2770, 7, 3688, label=FY23),
]


def states(quote, cells, claim, scales=MILLIONS):
    return quote_states(quote, cells, claim, scales)


# --- Positive cases that must keep repairing ---------------------------------

def test_the_demo_claim_is_supported_by_the_fy2025_cell():
    claim = "NVIDIA's Data Center revenue in fiscal 2025 was $115.2 billion"
    assert states(DATA_CENTER_ROW, DATA_CENTER, claim)


def test_the_follow_up_claim_is_supported_by_the_fy2024_cell():
    assert states(DATA_CENTER_ROW, DATA_CENTER, "In fiscal 2024 it was $47.5 billion")
    assert states(DATA_CENTER_ROW, DATA_CENTER, "FY24 Data Center revenue was $47.5 billion")


def test_compute_and_networking_are_each_met_by_their_own_row():
    claim = "In fiscal 2025, Compute ($102,196 million) and Networking ($12,990 million)"
    assert states(SEGMENT_ROWS, SEGMENTS, claim)


def test_two_spellings_of_one_value_are_met_by_one_cell():
    claim = "Data Center was $115,186 million ($115.2 billion)"
    assert states(DATA_CENTER_ROW, DATA_CENTER, claim)


def test_a_billions_table_matches_a_billion_claim_exactly():
    cells = [cell(1, 1, Decimal("115.2"))]
    assert states("Data Center 115.2", cells, "$115.2 billion", {1: Decimal(10) ** 9})


def test_a_thousands_table_matches_a_million_claim_one_unit_up():
    assert states(DATA_CENTER_ROW, DATA_CENTER, "$115.2 million", {1: Decimal(1000)})


def test_quote_text_figures_with_the_same_unit_and_more_decimals():
    quote = "Total net sales were 391.0 billion dollars"
    assert states(quote, [], "Total net sales were $391.0 billion")
    assert states(quote, [], "Total net sales were $391 billion")
    assert states("revenue of 115.19 billion", [], "revenue was $115.2 billion")
    assert states("gross margin percentage 46.6 % 45.2 %", [], "margin was 46.6%")


def test_a_per_share_figure_matches_an_unscaled_cell():
    cells = [cell(5, 3, Decimal("6.08"), scale_applies=False)]
    assert states("Diluted $ 6.08", cells, "diluted earnings per share were $6.08")


def test_claim_years_reads_full_and_short_years():
    assert claim_years("fiscal 2025 vs FY24, fiscal 23 and FY'22") == {
        "2025", "2024", "2023", "2022",
    }


# --- M1: the quote-text path -------------------------------------------------

def test_a_different_scale_unit_in_the_quote_text_does_not_count():
    assert not states("Total net sales were 391 million dollars", [], "sales were $391 billion")
    assert not states("an increase of 3.5 million", [], "an increase of $4 billion")


def test_a_bare_small_number_in_the_quote_does_not_count():
    assert not states("See Note 4 for details", [], "a charge of $4 billion")
    assert not states("Item 1A. Risk Factors", [], "exposure of $1 billion")


def test_rounding_never_crosses_the_whole_number():
    assert not states("an increase of 3.5 billion", [], "an increase of $4 billion")
    assert not states("grew 3.5% year over year", [], "grew 4%")


def test_rounding_never_adds_decimals():
    assert not states("revenue of 115.2 billion", [], "revenue was $115.19 billion")


# --- M2: the cell path -------------------------------------------------------

def test_a_thousands_table_cell_does_not_state_a_billion_claim():
    assert not states(DATA_CENTER_ROW, DATA_CENTER, "$115.2 billion", {1: Decimal(1000)})


def test_a_per_share_cell_does_not_state_a_billion_claim():
    unscaled = [cell(5, 3, Decimal("6.08"), scale_applies=False)]
    assert not states("Diluted $ 6.08", unscaled, "net income was $6.08 billion")
    scaled = [cell(5, 3, Decimal("6.08"))]
    assert not states("Diluted $ 6.08", scaled, "net income was $6.08 billion")


def test_an_unknown_scale_fails_closed():
    assert not states(DATA_CENTER_ROW, DATA_CENTER, "$115.2 billion", {1: None})
    assert not states(DATA_CENTER_ROW, DATA_CENTER, "$115.2 billion", {})


def test_a_cell_from_another_period_does_not_state_the_claim():
    claim = "In fiscal 2024 Data Center revenue was $115.2 billion"
    assert not states(DATA_CENTER_ROW, DATA_CENTER, claim)


def test_a_cell_with_no_column_label_fails_closed_when_the_claim_names_a_year():
    cells = [cell(2768, 2, 115186, label=None)]
    assert not states(DATA_CENTER_ROW, cells, "In fiscal 2025 it was $115.2 billion")
    unlabelled_year = [cell(2768, 2, 115186, label="Year Ended")]
    assert not states(DATA_CENTER_ROW, unlabelled_year, "In fiscal 2025 it was $115.2 billion")


def test_a_bare_figure_does_not_match_a_scaled_cell():
    # "$115,186" with no unit is ambiguous against a millions table.
    assert not states(DATA_CENTER_ROW, DATA_CENTER, "Data Center revenue was $115,186")


# --- L1: sign ----------------------------------------------------------------

def test_a_negative_cell_never_supports_a_repair():
    loss = [cell(9, 1, Decimal("-1234"), raw="(1,234)")]
    assert not states("Operating loss $ (1,234)", loss, "an operating loss of $1.234 billion")
    assert not states("Operating loss $ (1,234)", loss, "an operating loss of $1,234 million")


def test_a_negative_quote_figure_never_supports_a_repair():
    assert not states("net change of -1.2 billion", [], "a change of $1.2 billion")
    assert not states("net change of $(1.2) billion", [], "a change of $1.2 billion")
    assert not states("net change of (1.2) billion", [], "a decrease of $1.2 billion")


def test_a_table_figure_cannot_skip_the_period_check_through_the_quote_text():
    claim = "In fiscal 2024 Data Center revenue was 115,186"
    assert not states(DATA_CENTER_ROW, DATA_CENTER, claim)


# --- Period in prose (the quote-text path) -----------------------------------

def test_a_prose_quote_about_another_year_does_not_state_the_claim():
    quote = "Data Center revenue for fiscal year 2025 was $115.2 billion, up 142%"
    assert not states(quote, [], "Data Center revenue for fiscal 2024 was $115.2 billion")


def test_a_prose_quote_naming_any_year_outside_the_claim_fails_closed():
    quote = "Gross margin was 46.6% in 2025 and 45.2% in 2024"
    assert not states(quote, [], "Gross margin in 2024 was 46.6%")


def test_a_prose_quote_in_the_claims_year_still_counts():
    quote = "Data Center revenue for fiscal year 2025 was $115.2 billion"
    assert states(quote, [], "Data Center revenue for fiscal 2025 was $115.2 billion")
    assert states(quote, [], "FY25 Data Center revenue was $115.2 billion")
    assert states("revenue was $115.2 billion", [], "revenue was $115.2 billion")


# --- Direction words ---------------------------------------------------------

def test_a_loss_in_the_quote_does_not_state_income_in_the_claim():
    assert not states("a net loss of $1.2 billion", [], "net income was $1.2 billion")


def test_an_increase_claim_is_not_stated_by_a_decrease_quote():
    assert not states("revenue decreased 2% year over year", [], "revenue increased 2%")


def test_a_decrease_claim_needs_a_decrease_in_the_quote():
    assert not states("revenue grew 2% year over year", [], "revenue declined 2%")
    assert states("revenue decreased 2% year over year", [], "revenue fell 2%")


def test_a_decrease_claim_is_not_stated_by_a_plain_table_row():
    assert not states(DATA_CENTER_ROW, DATA_CENTER, "Data Center revenue fell to $115.2 billion")


def test_a_loss_row_does_not_state_an_income_claim():
    cells = [cell(9, 1, 1234)]
    assert not states("Net loss $ 1,234", cells, "net income was $1.234 billion")
    assert states("Net loss $ 1,234", cells, "a net loss of $1.234 billion")


# --- Period type: quarter vs year --------------------------------------------

Q2_SIX = "Six Months Ended › June 28, 2025"
Q2_THREE = "Three Months Ended › June 28, 2025"


def test_a_quarter_claim_is_not_stated_by_a_six_months_cell():
    cells = [cell(40, 1, 61000, label=Q2_SIX)]
    claim = "Revenue in Q2 fiscal 2025 was $61.0 billion"
    assert not states("Revenue $ 61,000", cells, claim)
    assert not states("Revenue $ 61,000", [cell(40, 1, 61000, label=FY25)], claim)


def test_a_quarter_claim_is_stated_by_a_three_months_cell():
    cells = [cell(40, 1, 61000, label=Q2_THREE)]
    assert states("Revenue $ 61,000", cells, "Revenue in Q2 fiscal 2025 was $61.0 billion")
    assert states("Revenue $ 61,000", cells,
                  "Second quarter fiscal 2025 revenue was $61.0 billion")


def test_a_full_year_claim_is_not_stated_by_a_months_cell():
    claim = "Full-year fiscal 2025 revenue was $61.0 billion"
    for label in (Q2_THREE, Q2_SIX, "Nine Months Ended › Sep 27, 2025"):
        assert not states("Revenue $ 61,000", [cell(40, 1, 61000, label=label)], claim)
    assert states("Revenue $ 61,000", [cell(40, 1, 61000, label="Year Ended › June 28, 2025")],
                  claim)


def test_a_quarter_claim_is_not_stated_by_full_year_prose():
    quote = "revenue for the year ended June 28, 2025 was $61.0 billion"
    assert not states(quote, [], "Revenue in Q2 fiscal 2025 was $61.0 billion")
    quarter = "revenue for the three months ended June 28, 2025 was $61.0 billion"
    assert not states(quarter, [], "Full-year fiscal 2025 revenue was $61.0 billion")
    assert states(quarter, [], "Revenue in Q2 fiscal 2025 was $61.0 billion")


def test_a_prose_quote_naming_no_year_does_not_state_a_dated_claim():
    # From the FY2025 10-K: true for fiscal 2025, silent about which year.
    quote = "Data Center revenue was $115.2 billion, up 142% from a year ago"
    assert not states(quote, [], "fiscal 2024 Data Center revenue was $115.2 billion")
    assert not states(quote, [], "In fiscal 2025 revenue was $115.2 billion")


def test_a_loss_word_anywhere_in_the_quote_counts():
    quote = "Net loss for fiscal year 2025 was $1.2 billion"
    assert not states(quote, [], "Net income for fiscal 2025 was $1.2 billion")
    assert not states("Net income for fiscal 2025 was $1.2 billion", [],
                      "Net loss for fiscal year 2025 was $1.2 billion")


def test_cash_used_in_is_not_cash_provided_by():
    assert not states("Net cash used in investing activities was $4.2 billion", [],
                      "Net cash provided by investing activities was $4.2 billion")
    assert states("Net cash used in investing activities was $4.2 billion", [],
                  "Investing activities used $4.2 billion of cash, net cash used in investing")


def test_an_expense_is_not_income():
    cells = [cell(9, 1, 1234)]
    assert not states("Other income (expense), net $ 1,234", cells,
                      "other income was $1.234 billion")
    assert states("Research and development expense $ 1,234", cells,
                  "R&D expense was $1.234 billion")
