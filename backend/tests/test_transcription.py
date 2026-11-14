"""The transcription guard (design §6.3 step 5): a figure-free claim's failed
quote may be repaired only into the passage it was garbling."""
from api.figures import figure_free_repair_accepted, transcription_matches

# Measured on the tutorial follow-up: the model stitched the FY2024 NVIDIA
# 10-K's table caption onto a row label.
GARBLED = "Revenue by each of the specialized markets we serve: Data Center"
CAPTION = (
    "The following table summarizes information pertaining to our revenue by each of "
    "the specialized markets we serve:"
)
# The real chunk text around that caption: accession 0001045810-24-000029
# (FY2024 10-K), sid 2773, read from the corpus.
NVDA_CHUNK = (
    "No customer represented 10% or more of total revenue for fiscal years 2023 and "
    "2022. The following table summarizes information pertaining to our revenue by "
    "each of the specialized markets we serve: Year Ended Jan 28, 2024 Jan 29, 2023 "
    "Jan 30, 2022 Revenue: (In millions) Data Center $ 47,525 $ 15,005 $ 10,613 "
    "Gaming 10,447 9,067 12,462 Professional Visualization 1,553 1,544 2,111 "
    "Automotive 1,091 903 566 OEM and Other 306 455 1,162 Total revenue $ 60,922 "
    "$ 26,974 $ 26,914"
)


def accepted(original, repaired, excerpt=None):
    """The guard, with an excerpt that holds every word of both quotes unless
    a test says otherwise -- so a rejection is down to the rule under test."""
    return figure_free_repair_accepted(
        original, repaired, excerpt if excerpt is not None else f"{original} {repaired}"
    )


def test_the_nvda_caption_example_is_accepted():
    # "revenue by each of the specialized markets we serve:" is 52 of 64.
    assert transcription_matches(GARBLED, CAPTION, NVDA_CHUNK)
    assert figure_free_repair_accepted(GARBLED, CAPTION, NVDA_CHUNK)


def test_the_whole_caption_sentence_and_its_shorter_form_are_accepted():
    # The repairer may return the caption from its start (8 extra words before
    # the run) or from "our"; the failed quote's extra words come after it.
    assert figure_free_repair_accepted(GARBLED, CAPTION, NVDA_CHUNK)
    shorter = "our revenue by each of the specialized markets we serve:"
    assert figure_free_repair_accepted(GARBLED, shorter, NVDA_CHUNK)


def test_a_different_sentence_from_the_same_chunk_is_rejected():
    other = "No customer represented 10% or more of total revenue for fiscal years 2023"
    assert not transcription_matches(GARBLED, other, NVDA_CHUNK)
    assert not figure_free_repair_accepted(GARBLED, other, NVDA_CHUNK)


def test_a_mostly_invented_quote_is_rejected_even_if_part_of_it_is_real():
    # The real 52 characters are under 60% of this 100-character quote.
    padded = GARBLED + ", our largest and fastest-growing market segment"
    assert not transcription_matches(padded, CAPTION, NVDA_CHUNK)


def test_a_short_quote_never_qualifies():
    # Complete overlap, but under 20 characters: too little to call it "there".
    assert not accepted("Data Center revenue", "Data Center revenue")
    assert not accepted("Data Center", "Data Center")


def test_case_and_typography_differences_alone_are_accepted():
    original = "the company’s “specialized markets” — data center and gaming"
    repaired = "The Company's \"specialized markets\" - Data Center and Gaming"
    assert transcription_matches(original, repaired, repaired)
    assert figure_free_repair_accepted(original, repaired, repaired)


def test_a_hyphen_for_a_space_is_typography_too():
    assert accepted("Services revenue reached an all time record",
                    "Services revenue reached an all-time record.")


def test_a_repaired_quote_stating_a_figure_is_rejected_for_a_figure_free_claim():
    original = "Data Center revenue for fiscal year 2024 was a record"
    repaired = "Data Center revenue for fiscal year 2024 was a record $47.5 billion"
    assert transcription_matches(original, repaired, repaired)
    assert not figure_free_repair_accepted(original, repaired, repaired)


def test_a_bare_year_or_small_number_is_not_a_figure_here():
    excerpt = (
        "Item 7. Management's Discussion and Analysis of Financial Condition and Results"
        " of Operations for fiscal 2024"
    )
    original = "Discussion and Analysis of Financial Condition for fiscal 2024"
    repaired = "Item 7. Management's Discussion and Analysis of Financial Condition"
    assert figure_free_repair_accepted(original, repaired, excerpt)


def test_an_empty_original_or_excerpt_never_qualifies():
    assert not transcription_matches("", CAPTION, NVDA_CHUNK)
    assert not transcription_matches("   ", CAPTION, NVDA_CHUNK)
    assert not transcription_matches(GARBLED, CAPTION, "")
    assert not figure_free_repair_accepted(GARBLED, CAPTION, None)


# --- A repair may not change what the quote says ------------------------------

EXPECT = "We expect the adoption of this guidance to have a material impact"
NOT_EXPECT = "We do not expect the adoption of this guidance to have a material impact"


def test_a_repair_may_not_drop_a_negation():
    assert not accepted(NOT_EXPECT, EXPECT)


def test_a_repair_may_not_add_a_negation():
    assert not accepted(EXPECT, NOT_EXPECT)


def test_a_repair_may_not_flip_a_direction():
    assert not accepted(
        "Data Center revenue increased due to strong demand for our Hopper platform",
        "Data Center revenue decreased due to strong demand for our Hopper platform",
    )


def test_a_repair_may_not_drop_an_invented_tail():
    assert not accepted(
        "Revenue growth was limited by supply constraints and increased",
        "Revenue growth was limited by supply constraints",
    )
    assert not accepted(
        "Revenue came from our Hopper and Blackwell platforms which we no longer sell",
        "Revenue came from our Hopper and Blackwell platforms",
    )


def test_a_repair_may_not_append_new_material():
    fragment = "Our Data Center platform serves hyperscalers"
    extended = (
        fragment + " and enterprises, and it was the primary driver of the company's"
        " record results this year."
    )
    assert not accepted(fragment, extended)


def test_the_run_is_trimmed_to_whole_words():
    # Without trimming the run would end on the "in" of "increased", leaving
    # "creased" as the only extra text -- and no direction word to catch.
    assert not accepted(
        "Gaming revenue was driven by sales to retail partners in",
        "Gaming revenue was driven by sales to retail partners increased",
    )


# --- Edges only: a repair may trim or extend, never substitute ----------------

def test_a_repair_may_not_swap_the_subject():
    assert not accepted(
        "Gaming revenue was driven by strong demand for Blackwell",
        "Data Center revenue was driven by strong demand for Blackwell",
    )


def test_a_repair_may_not_swap_the_company():
    assert not accepted(
        "Microsoft is our largest customer, accounting for a significant share of revenue",
        "Amazon is our largest customer, accounting for a significant share of revenue",
    )


def test_a_repair_may_not_swap_the_year():
    assert not accepted(
        "In fiscal 2024, we adopted the new accounting guidance on segment reporting",
        "In fiscal 2025, we adopted the new accounting guidance on segment reporting",
    )


def test_a_repair_may_not_swap_the_modal():
    assert not accepted(
        "We will discontinue the legacy product line at the end of the period",
        "We may discontinue the legacy product line at the end of the period",
    )


def test_words_the_repair_drops_must_be_in_the_excerpt():
    original = "Demand for our Blackwell platform was strong in China"
    repaired = "Demand for our Blackwell platform was strong"
    excerpt = "Demand for our Blackwell platform was strong in every region we serve."
    assert not figure_free_repair_accepted(original, repaired, excerpt)
    # The same trim is fine when the dropped words are real text of the chunk.
    assert figure_free_repair_accepted(original, repaired, excerpt + " Sales in China fell.")
