from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from .canonicalize import Sentence
from .tables import Cell, TableInfo, row_contexts

# bge-small-en-v1.5 truncates input at 512 of its own tokens; 450 tiktoken
# tokens keeps chunks safely under that limit (amends design §4.3's ~600).
# For table chunks the budget covers context + text, since both are embedded.
MAX_TOKENS = 450


@dataclass(frozen=True)
class Chunk:
    section: str
    sid_start: int
    sid_end: int
    text: str
    token_count: int
    # Spec 2026-09-29 §5: embedded, lexically indexed and shown to the model,
    # but never part of `text`, so verification offsets are untouched.
    context: str = ""
    # Set only on the pieces of a split table; drives the retrieval cap.
    table_id: int | None = None


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def embed_input(chunk: Chunk) -> str:
    """What the embedder sees: context first, so a split table's column
    meaning survives into the vector."""
    return f"{chunk.context}\n{chunk.text}" if chunk.context else chunk.text


def _context(rows: list[Sentence], contexts: dict[int, str]) -> str:
    """One line per table in the chunk, from that table's first data row."""
    lines: list[str] = []
    seen: set[int] = set()
    for s in rows:
        if s.table_id is None or s.table_id in seen or s.sid not in contexts:
            continue
        seen.add(s.table_id)
        if contexts[s.sid]:
            lines.append(contexts[s.sid])
    return "\n".join(lines)


def _make(rows: list[Sentence], contexts: dict[int, str], table_id: int | None = None) -> Chunk:
    text = " ".join(s.text for s in rows)
    return Chunk(
        section=rows[0].section,
        sid_start=rows[0].sid,
        sid_end=rows[-1].sid,
        text=text,
        token_count=count_tokens(text),
        context=_context(rows, contexts),
        table_id=table_id,
    )


def _split_table(
    rows: list[Sentence], contexts: dict[int, str], max_tokens: int
) -> list[Chunk]:
    """Spec §5.2: break at row boundaries only; prefer to start a piece where
    a new header or group block begins; never leave header rows stranded at
    the end of a piece, away from the data they label."""
    table_id = rows[0].table_id
    ctx_tokens = max(
        (count_tokens(contexts[s.sid]) for s in rows if s.sid in contexts), default=0
    )
    budget = max_tokens - ctx_tokens
    tokens = {s.sid: count_tokens(s.text) for s in rows}
    pieces: list[Chunk] = []
    piece: list[Sentence] = []

    def has_data(part: list[Sentence]) -> bool:
        return any(s.sid in contexts for s in part)

    def close() -> None:
        nonlocal piece
        carry: list[Sentence] = []
        while piece and piece[-1].sid not in contexts and has_data(piece[:-1]):
            carry.insert(0, piece.pop())
        if piece:
            pieces.append(_make(piece, contexts, table_id))
        piece = carry

    for k, current in enumerate(rows):
        is_data = current.sid in contexts
        used = sum(tokens[s.sid] for s in piece)
        block_starts = not is_data and bool(piece) and piece[-1].sid in contexts
        if block_starts:
            block: list[Sentence] = []
            for later in rows[k:]:
                if block and later.sid not in contexts and block[-1].sid in contexts:
                    break
                block.append(later)
            if used + sum(tokens[s.sid] for s in block) > budget:
                close()
        elif has_data(piece) and used + tokens[current.sid] > budget:
            close()
        piece.append(current)
    if piece:
        pieces.append(_make(piece, contexts, table_id))
    return pieces


def chunk_sentences(
    sentences: list[Sentence],
    *,
    max_tokens: int = MAX_TOKENS,
    tables: dict[int, TableInfo] | None = None,
    cells: list[Cell] | None = None,
) -> list[Chunk]:
    """Greedy grouping of consecutive sentences within a section (design §4.3).

    Chunks are contiguous, disjoint sid ranges. A table is costed as a whole,
    context included: one that fits joins the greedy run like any sentence;
    an over-budget one stays atomic unless its header was parsed
    (splittable), in which case it is isolated and split by _split_table.
    Without `tables`/`cells` every table is unsplittable and context-free,
    which is exactly the pre-2026-09-29 behaviour."""
    tables = tables or {}
    contexts = row_contexts(tables, cells or [])
    chunks: list[Chunk] = []
    current: list[Sentence] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            chunks.append(_make(current, contexts))
            current, current_tokens = [], 0

    i = 0
    while i < len(sentences):
        first = sentences[i]
        j = i + 1
        if first.table_id is not None:
            while j < len(sentences) and sentences[j].table_id == first.table_id:
                j += 1
        unit = sentences[i:j]
        cost = sum(count_tokens(s.text) for s in unit)
        ctx = _context(unit, contexts)
        if ctx:
            cost += count_tokens(ctx)
        info = tables.get(first.table_id) if first.table_id is not None else None
        if info is not None and info.splittable and cost > max_tokens:
            flush()
            chunks.extend(_split_table(unit, contexts, max_tokens))
            i = j
            continue
        new_section = current and first.section != current[0].section
        over_budget = current and current_tokens + cost > max_tokens
        if new_section or over_budget:
            flush()
        current.extend(unit)
        current_tokens += cost
        i = j
    flush()
    return chunks
