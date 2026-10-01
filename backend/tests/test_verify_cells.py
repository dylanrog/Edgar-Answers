from datetime import date
from pathlib import Path

from api.retrieval import RetrievedChunk
from api.verify import Citation, verify_citation
from pipeline.canonicalize import canonicalize

FIXTURES = Path(__file__).parent / "fixtures"
CANONICAL = canonicalize(
    (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8"), "10-K"
)
SENTENCES = CANONICAL.sentences
TEXT = " ".join(s.text for s in SENTENCES)
DATA_CENTER = next(s.sid for s in SENTENCES if s.text.startswith("Data Center"))


def chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=1, accession="A-1", form_type="10-K", filing_date=date(2025, 2, 26),
        ticker="NVDA", section="item7", sid_start=SENTENCES[0].sid,
        sid_end=SENTENCES[-1].sid, text=TEXT, filing_id=1, score=0.5,
    )


def verify(quote, cells=CANONICAL.cells):
    return verify_citation(Citation(1, 1, quote), chunk(), SENTENCES, cells)


def test_a_quote_ending_at_a_figure_resolves_that_cell():
    result = verify("Data Center $ 115,186")
    assert result.verified
    assert result.sids == [DATA_CENTER]
    assert result.cells == ((DATA_CENTER, 2),)


def test_a_quote_spanning_two_figures_resolves_both():
    result = verify("Data Center $ 115,186 $ 47,525")
    assert result.cells == ((DATA_CENTER, 2), (DATA_CENTER, 6))


def test_a_quote_of_only_the_row_label_resolves_the_row_and_no_cells():
    result = verify("Data Center")
    assert result.sids == [DATA_CENTER]
    assert result.cells == ()


def test_a_prose_quote_resolves_no_cells():
    result = verify("The following table summarizes revenue by specialized markets:")
    assert result.verified
    assert result.cells == ()


def test_cells_without_spans_fall_back_to_the_row():
    from dataclasses import replace

    spanless = [replace(c, char_start=None, char_end=None) for c in CANONICAL.cells]
    result = verify("Data Center $ 115,186", spanless)
    assert result.sids == [DATA_CENTER]
    assert result.cells == ()


def test_an_unverified_quote_resolves_no_cells():
    result = verify("Data Center $ 999,999")
    assert not result.verified
    assert result.cells == ()
