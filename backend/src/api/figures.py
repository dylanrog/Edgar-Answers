"""Which of a citation's table figures the answer actually states.

A quote of a whole multi-year row ("Data Center $ 115,186 $ 47,525 $ 15,005")
verifies, and resolves every figure it covers -- so the viewer would mark all
three years beside an answer about one of them. This narrows the *displayed*
cells to the figures the answer's own words state. It never touches
`verified` or `sids`: verification is the verbatim quote check (design §6.3),
and this is emphasis only.
"""
from __future__ import annotations

import dataclasses
import re
from decimal import ROUND_HALF_UP, Decimal

from pipeline.tables import Cell

from .verify import VerifiedCitation

_MARKER = re.compile(r"\[(\d+)\]")
# Whole figures only: "115,186", "115.2", "2025". A lookbehind/lookahead keeps
# "115,186" from also yielding "186" and "1.2.3" from yielding "2.3".
_FIGURE = re.compile(
    r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+(?![\d,])|\d+(?!\d))(?:\.\d+)?"
)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|\n")
# What follows a marker group that closes its sentence: "... [2]." or "... [2]".
_CLOSES_SENTENCE = re.compile(r"[ \t]*(?:[.!?\n]|$)")
_SEPARATORS = " ,;\t"


def _groups(prose: str) -> list[tuple[int, int, set[int]]]:
    """Runs of adjacent markers ("[1][2]", "[1], [2]") as (start, end, markers)."""
    groups: list[tuple[int, int, set[int]]] = []
    for match in _MARKER.finditer(prose):
        if groups and not prose[groups[-1][1] : match.start()].strip(_SEPARATORS):
            first, _, markers = groups[-1]
            groups[-1] = (first, match.end(), markers | {int(match.group(1))})
        else:
            groups.append((match.start(), match.end(), {int(match.group(1))}))
    return groups


def _before(prose: str, start: int, previous_end: int) -> str:
    """The words a marker group closes: back to the previous group or sentence
    break, whichever is later."""
    # "...$115.2 billion. [1]": the period before the marker ends the claim,
    # it doesn't start an empty new one.
    text = prose[previous_end:start].rstrip().rstrip(".!?:").rstrip()
    return _SENTENCE_BREAK.split(text)[-1]


def _after(prose: str, end: int, next_group: tuple[int, int, set[int]] | None) -> str:
    """The words a marker group opens: up to the next group or sentence end.
    Text that runs into a group which itself ends a sentence ("X. [1] Y was
    $47.5 billion [2].") is that group's claim, not this one's."""
    limit = next_group[0] if next_group else len(prose)
    text = prose[end:limit]
    sentence_end = _SENTENCE_END.search(text)
    if sentence_end:
        return text[: sentence_end.start()]
    if next_group and _CLOSES_SENTENCE.match(prose[next_group[1] :]):
        return ""
    return text


def claim_sides(prose: str, marker: int) -> tuple[str, str]:
    """(preferred, fallback) text for the claim `[marker]` cites. A marker that
    starts a sentence ("comparison: [1] Revenue was $115.2 billion.") cites the
    words after it; otherwise ("Revenue was $115.2 billion [1].") the words
    before it. The other side is the fallback ("Per the 10-K [1], revenue was
    ..."). Every occurrence of the marker contributes; a marker absent from the
    prose falls back to the whole prose."""
    preferred: list[str] = []
    fallback: list[str] = []
    groups = _groups(prose)
    previous_end = 0
    claimed_until = 0  # words up to here belong to an earlier group
    for i, (start, end, markers) in enumerate(groups):
        lead = prose[previous_end:start].rstrip(" \t")
        starts_sentence = not lead.strip() or lead[-1] in ".!?:\n"
        # Words the previous group opened are its claim, not this group's.
        before = _before(prose, start, max(previous_end, claimed_until))
        after = _after(prose, end, groups[i + 1] if i + 1 < len(groups) else None)
        if marker in markers:
            first, second = (after, before) if starts_sentence else (before, after)
            preferred.append(first)
            fallback.append(second)
        claimed_until = end + len(after) if starts_sentence and after.strip() else end
        previous_end = end
    if not preferred:
        return prose, ""
    return " ".join(preferred), " ".join(fallback)


def claim_text(prose: str, marker: int) -> str:
    """The words each `[marker]` closes (the before side only). Adjacent
    markers ("[1][2]") share one claim. Falls back to the whole prose if the
    marker never appears."""
    segments: list[str] = []
    groups = _groups(prose)
    previous_end = 0
    for start, end, markers in groups:
        if marker in markers:
            segments.append(_before(prose, start, previous_end))
        previous_end = end
    return " ".join(segments) if segments else prose


def stated_figures(text: str) -> list[Decimal]:
    return [Decimal(token.replace(",", "")) for token in _FIGURE.findall(text)]


def is_stated(cell: Cell, figures: list[Decimal]) -> bool:
    """The cell's value appears among `figures` as its exact digits, or -- for a
    plain number, typically in millions -- in thousands of its unit rounded to
    the 1-3 decimals the prose used ("115,186" ~ "115.186", "115.19", "115.2")."""
    if cell.value is None:
        return False
    value = abs(cell.value)
    for figure in figures:
        if figure == value:
            return True
        places = -figure.as_tuple().exponent
        if cell.kind == "number" and 1 <= places <= 3:
            scaled = (value / 1000).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)
            if figure == scaled:
                return True
    return False


def narrow_cells(
    citation: VerifiedCitation, cells: list[Cell], prose: str
) -> VerifiedCitation:
    """Keep only the cited cells whose figure the claim around the citation's
    marker states -- the preferred side of the marker, else the other side.
    A citation with one cell or none, or whose cells the claim never states,
    is returned unchanged -- a citation is never hidden."""
    if len(citation.cells) < 2:
        return citation
    by_key = {(cell.sid, cell.cell_index): cell for cell in cells}
    for side in claim_sides(prose, citation.marker):
        figures = stated_figures(_MARKER.sub(" ", side))
        kept = tuple(
            key for key in citation.cells if key in by_key and is_stated(by_key[key], figures)
        )
        if kept:
            return dataclasses.replace(citation, cells=kept)
    return citation
