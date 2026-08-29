from pipeline.canonicalize import Sentence
from pipeline.chunk import MAX_TOKENS, chunk_sentences


def row(sid: int, table_id: int, text: str) -> Sentence:
    return Sentence(sid, "item7", text, 0, len(text), table_id)


def prose(sid: int, text: str) -> Sentence:
    return Sentence(sid, "item7", text, 0, len(text), None)


def test_a_table_stays_in_one_chunk_even_past_the_token_budget():
    """Splitting a table separates the header row from its numbers, leaving
    the model with 'Europe 94,294 (1) %' and no column meaning."""
    rows = [row(i, 1, f"Region {i} $ {i},000 {i} % $ {i},500 " + "filler " * 40) for i in range(40)]
    chunks = chunk_sentences(rows)
    assert len(chunks) == 1
    assert chunks[0].token_count > MAX_TOKENS
    assert chunks[0].sid_start == 0
    assert chunks[0].sid_end == 39


def test_two_tables_do_not_merge_into_one_chunk():
    sentences = [row(0, 1, "Americas $ 1"), row(1, 1, "Europe $ 2"), row(2, 2, "Other table $ 3")]
    chunks = chunk_sentences(sentences, max_tokens=6)
    assert [(c.sid_start, c.sid_end) for c in chunks] == [(0, 1), (2, 2)]


def test_chunks_stay_contiguous_and_disjoint():
    """Spec invariant 6. Table atomicity must not introduce overlap: an
    overlapping sid would belong to two chunks, and the header-repetition
    design was rejected precisely to avoid that."""
    sentences = (
        [prose(0, "Intro sentence.")]
        + [row(i, 1, f"Region {i} $ {i},000") for i in range(1, 30)]
        + [prose(30, "Closing sentence.")]
    )
    chunks = chunk_sentences(sentences, max_tokens=20)
    covered = [sid for c in chunks for sid in range(c.sid_start, c.sid_end + 1)]
    assert covered == list(range(31)), "sids must be covered once, in order"


def test_prose_still_splits_on_the_token_budget():
    sentences = [prose(i, f"Sentence number {i} here.") for i in range(20)]
    chunks = chunk_sentences(sentences, max_tokens=20)
    assert len(chunks) > 1


def test_chunk_text_is_the_space_join_of_its_sentences():
    """verify.sentence_spans reconstructs chunk text this way to map a quote
    back to sids. If the two ever disagree, citations resolve to wrong sids."""
    sentences = [prose(0, "First one."), row(1, 1, "Americas $ 1"), row(2, 1, "Europe $ 2")]
    chunks = chunk_sentences(sentences)
    assert chunks[0].text == "First one. Americas $ 1 Europe $ 2"
