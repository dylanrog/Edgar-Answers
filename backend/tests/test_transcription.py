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


def test_the_nvda_caption_example_is_accepted():
    # "revenue by each of the specialized markets we serve:" is 52 of 64.
    assert transcription_matches(GARBLED, CAPTION)
    assert figure_free_repair_accepted(GARBLED, CAPTION)


def test_a_different_sentence_from_the_same_chunk_is_rejected():
    other = "Data Center revenue was driven by demand for our Hopper GPU computing platform."
    assert not transcription_matches(GARBLED, other)
    assert not figure_free_repair_accepted(GARBLED, other)


def test_a_mostly_invented_quote_is_rejected_even_if_part_of_it_is_real():
    # The real 52 characters are under 60% of this 100-character quote.
    padded = GARBLED + ", our largest and fastest-growing market segment"
    assert not transcription_matches(padded, CAPTION)


def test_a_short_quote_never_qualifies():
    # Complete overlap, but under 20 characters: too little to call it "there".
    assert not transcription_matches("Data Center revenue", "Data Center revenue grew")
    assert not figure_free_repair_accepted("Data Center", "Data Center")


def test_case_and_typography_differences_alone_are_accepted():
    original = "the company’s “specialized markets” — data center and gaming"
    repaired = "The Company's \"specialized markets\" - Data Center and Gaming"
    assert transcription_matches(original, repaired)
    assert figure_free_repair_accepted(original, repaired)


def test_a_repaired_quote_stating_a_figure_is_rejected_for_a_figure_free_claim():
    original = "Data Center revenue for fiscal year 2024 was a record, up from a year ago"
    repaired = "Data Center revenue for fiscal year 2024 was a record $47.5 billion, up 217%"
    assert transcription_matches(original, repaired)
    assert not figure_free_repair_accepted(original, repaired)


def test_a_bare_year_or_small_number_is_not_a_figure_here():
    original = "Item 7. Management's Discussion and Analysis for fiscal 2024 overview"
    repaired = "Item 7. Management's Discussion and Analysis of Financial Condition"
    assert figure_free_repair_accepted(original, repaired)


def test_an_empty_original_never_qualifies():
    assert not transcription_matches("", CAPTION)
    assert not transcription_matches("   ", CAPTION)
