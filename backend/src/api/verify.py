from __future__ import annotations

from dataclasses import dataclass

from pipeline.canonicalize import Sentence
from pipeline.tables import Cell

from .normalize import normalize
from .retrieval import RetrievedChunk


@dataclass(frozen=True)
class Citation:
    """A citation as the model claimed it, before verification."""

    marker: int
    chunk_id: int
    quote: str


@dataclass(frozen=True)
class VerifiedCitation:
    marker: int
    chunk_id: int
    quote: str
    verified: bool
    accession: str
    sids: list[int]
    ticker: str = ""
    form_type: str = ""
    filing_date: str = ""
    # (sid, cell_index) of each table figure the quote covers (spec 2026-09-29
    # §5.7). cell_index is the browser's tr.cells[i] in the stored viewer HTML.
    cells: tuple[tuple[int, int], ...] = ()


def sentence_spans(sentences: list[Sentence]) -> list[tuple[int, int, int]]:
    """(sid, start, end) half-open spans in *chunk-text* coordinates.

    This reconstructs the ' '.join(...) that pipeline.chunk.chunk_sentences
    used to build chunk.text. It deliberately does not use Sentence.char_start,
    which is an offset into the filing's canonical text (a '\\n'-joined string).
    Those two coordinate systems agree today only because both joins happen to
    use exactly one separator character; depending on that coincidence would
    make verification break silently if either join ever changed.
    """
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    for sentence in sentences:
        spans.append((sentence.sid, cursor, cursor + len(sentence.text)))
        cursor += len(sentence.text) + 1  # the single space from " ".join
    return spans


def find_quote(chunk_text: str, quote: str) -> tuple[int, int] | None:
    """Normalized substring match; returns original chunk_text offsets.

    No fuzzy matching (design §6.3) -- determinism is the whole point. A quote
    that does not appear verbatim modulo normalization is unverified, full stop.
    """
    normalized_quote, _ = normalize(quote)
    normalized_quote = normalized_quote.strip()
    if not normalized_quote:
        return None
    normalized_chunk, offsets = normalize(chunk_text)
    index = normalized_chunk.find(normalized_quote)
    if index == -1:
        return None
    start = offsets[index]
    end = offsets[index + len(normalized_quote) - 1] + 1
    return start, end


def resolve_sids(spans: list[tuple[int, int, int]], start: int, end: int) -> list[int]:
    """Every sid whose span overlaps [start, end)."""
    return [
        sid for sid, span_start, span_end in spans if span_start < end and start < span_end
    ]


def resolve_cells(
    spans: list[tuple[int, int, int]], start: int, end: int, cells: list[Cell]
) -> list[tuple[int, int]]:
    """(sid, cell_index) for every stored figure the match [start, end) covers.

    Cell spans are relative to their row sentence; `spans` places each row
    sentence in chunk-text coordinates, so the sum lines them up. A cell
    without a span (its row did not reproduce cell by cell) is skipped -- the
    row itself still resolves through its sid.
    """
    row_start = {sid: span_start for sid, span_start, _ in spans}
    hits: list[tuple[int, int]] = []
    for cell in cells:
        if cell.char_start is None or cell.char_end is None or cell.sid not in row_start:
            continue
        cell_start = row_start[cell.sid] + cell.char_start
        cell_end = row_start[cell.sid] + cell.char_end
        if cell_start < end and start < cell_end:
            hits.append((cell.sid, cell.cell_index))
    return hits


def verify_citation(
    citation: Citation,
    chunk: RetrievedChunk,
    sentences: list[Sentence],
    cells: list[Cell] | tuple[Cell, ...] = (),
) -> VerifiedCitation:
    match = find_quote(chunk.text, citation.quote)
    spans = sentence_spans(sentences)
    sids = resolve_sids(spans, *match) if match else []
    figures = resolve_cells(spans, *match, list(cells)) if match and sids else []
    return VerifiedCitation(
        marker=citation.marker,
        chunk_id=citation.chunk_id,
        quote=citation.quote,
        verified=bool(sids),
        accession=chunk.accession,
        sids=sids,
        ticker=chunk.ticker,
        form_type=chunk.form_type,
        filing_date=chunk.filing_date.isoformat(),
        cells=tuple(figures),
    )
