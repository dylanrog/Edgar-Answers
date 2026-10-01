from datetime import date

from api.retrieval import MAX_CHUNKS_PER_TABLE, RetrievedChunk, cap_per_table
from api.verify import Citation, verify_citation
from pipeline.canonicalize import Sentence


def test_a_table_gets_at_most_two_slots_and_the_rest_back_fill():
    ranked = [(i, 1.0 - i / 100) for i in range(1, 11)]
    table_of = {i: (7, 3) if i <= 5 else None for i in range(1, 11)}
    kept = cap_per_table(ranked, table_of, k_final=4)
    assert [chunk_id for chunk_id, _ in kept] == [1, 2, 6, 7]
    assert MAX_CHUNKS_PER_TABLE == 2


def test_the_same_table_id_in_two_filings_is_two_tables():
    ranked = [(1, 0.9), (2, 0.8), (3, 0.7), (4, 0.6)]
    table_of = {1: (7, 3), 2: (7, 3), 3: (8, 3), 4: (8, 3)}
    assert [c for c, _ in cap_per_table(ranked, table_of, k_final=4)] == [1, 2, 3, 4]


def test_order_is_preserved_and_short_lists_are_fine():
    ranked = [(5, 0.9), (6, 0.5)]
    assert cap_per_table(ranked, {5: None, 6: None}, k_final=8) == ranked


def test_a_quote_found_only_in_context_is_unverified():
    """Spec §5.4: context is shown to the model but never verified against."""
    sentence = Sentence(0, "item7", "Data Center $ 115,186", 0, 21, 1)
    chunk = RetrievedChunk(
        chunk_id=1, accession="A-1", form_type="10-K", filing_date=date(2025, 2, 26),
        ticker="NVDA", section="item7", sid_start=0, sid_end=0,
        text=sentence.text, filing_id=1, score=0.5,
        context="Table: Revenue by market | Scale: in millions", table_id=None,
    )
    result = verify_citation(Citation(1, 1, "Revenue by market"), chunk, [sentence])
    assert result.verified is False
