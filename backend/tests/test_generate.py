from datetime import date

from api.generate import AnswerSplitter, build_user_message, parse_citations
from api.retrieval import RetrievedChunk


def chunk(chunk_id: int, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        accession="0000320193-24-000123",
        form_type="10-K",
        filing_date=date(2024, 11, 1),
        ticker="AAPL",
        section="item7",
        sid_start=0,
        sid_end=1,
        text=text,
        filing_id=1,
        score=0.5,
    )


def test_user_message_labels_each_chunk_with_its_id_and_provenance():
    message = build_user_message("What were net sales?", [chunk(42, "Net sales rose.")])
    assert "chunk_id=42" in message
    assert "AAPL" in message and "10-K" in message
    assert "Net sales rose." in message
    assert "What were net sales?" in message


def test_splitter_emits_prose_and_withholds_the_fence():
    splitter = AnswerSplitter()
    parts = ["Net sales ", "rose [1].\n\n", "```json\n", '{"citations": []}', "\n```"]
    emitted = "".join(splitter.feed(part) for part in parts)
    emitted += splitter.finish()
    assert emitted == "Net sales rose [1].\n\n"
    assert "```json" in splitter.raw


def test_splitter_never_emits_a_partial_fence_opener():
    splitter = AnswerSplitter()
    first = splitter.feed("Answer.\n\n`")
    assert "`" not in first


def test_splitter_with_no_fence_emits_everything_on_finish():
    splitter = AnswerSplitter()
    emitted = splitter.feed("Just prose, no citations.") + splitter.finish()
    assert emitted == "Just prose, no citations."


def test_parse_citations_reads_the_fenced_block():
    raw = (
        "Answer [1].\n\n```json\n"
        '{"citations": [{"marker": 1, "chunk_id": 8, "quote": "hi"}]}\n```'
    )
    citations = parse_citations(raw)
    assert len(citations) == 1
    assert citations[0].marker == 1
    assert citations[0].chunk_id == 8
    assert citations[0].quote == "hi"


def test_parse_citations_accepts_a_bare_json_fence():
    assert parse_citations('Answer.\n```\n{"citations": []}\n```') == []


def test_parse_citations_returns_none_when_no_block_is_present():
    assert parse_citations("Answer with no block at all.") is None


def test_parse_citations_returns_none_on_malformed_json():
    assert parse_citations('Answer.\n```json\n{"citations": [oops}\n```') is None


def test_parse_citations_skips_entries_missing_required_fields():
    raw = (
        '```json\n{"citations": [{"marker": 1},'
        ' {"marker": 2, "chunk_id": 3, "quote": "q"}]}\n```'
    )
    citations = parse_citations(raw)
    assert [c.marker for c in citations] == [2]


def test_system_prompt_asks_for_every_supporting_filing():
    from api.generate import SYSTEM_PROMPT

    assert "more than one filing" in SYSTEM_PROMPT


def test_multi_filing_rule_precedes_the_output_format_example():
    """Placement is load-bearing, and was measured rather than assumed.

    With this rule *after* the fenced JSON example, the golden set's q004
    dropped its citation block entirely on 3 of 3 eval runs: the model
    enumerated the excerpts inline, counted that as having cited them, and
    stopped before the block. Moved ahead of the example -- so the output
    format stays contiguous and last -- q004 emitted citations on 2 of 2.
    """
    from api.generate import FENCE, SYSTEM_PROMPT

    assert SYSTEM_PROMPT.index("more than one filing") < SYSTEM_PROMPT.index(FENCE)


def test_a_table_chunk_shows_each_context_line_labelled_above_its_text():
    from dataclasses import replace

    from api.generate import CONTEXT_LABEL

    table = replace(
        chunk(7, "Data Center $ 115,186 $ 47,525"),
        context="Table: First | Columns: 2025; 2024\nTable: Second | Columns: 2025",
    )
    message = build_user_message("Data Center revenue?", [table])
    lines = message.splitlines()
    header = next(i for i, line in enumerate(lines) if "chunk_id=7" in line)
    assert lines[header + 1] == f"{CONTEXT_LABEL} Table: First | Columns: 2025; 2024"
    assert lines[header + 2] == f"{CONTEXT_LABEL} Table: Second | Columns: 2025"
    assert lines[header + 3] == "Data Center $ 115,186 $ 47,525"


def test_a_prose_chunk_has_no_context_line():
    from api.generate import CONTEXT_LABEL

    message = build_user_message("Why?", [chunk(8, "Net sales rose.")])
    assert CONTEXT_LABEL not in message


def test_system_prompt_forbids_quoting_context_before_the_output_example():
    from api.generate import FENCE, SYSTEM_PROMPT

    assert "never quote them" in SYSTEM_PROMPT
    assert "through that figure" in SYSTEM_PROMPT
    assert SYSTEM_PROMPT.index("never quote them") < SYSTEM_PROMPT.index(FENCE)
    assert SYSTEM_PROMPT.index("through that figure") < SYSTEM_PROMPT.index(FENCE)
