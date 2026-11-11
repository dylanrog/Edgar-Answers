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
_SEPARATORS = " ,;\t"


def claim_text(prose: str, marker: int) -> str:
    """The words each `[marker]` closes: from the previous marker or sentence
    break, whichever is later, up to the marker. Adjacent markers ("[1][2]")
    share one claim. Falls back to the whole prose if the marker never appears.
    """
    segments: list[str] = []
    claim = ""
    previous_end = 0
    for match in _MARKER.finditer(prose):
        gap = prose[previous_end : match.start()]
        if gap.strip(_SEPARATORS):
            # "...$115.2 billion. [1]": the period before the marker ends the
            # claim, it doesn't start an empty new one.
            text = gap.rstrip().rstrip(".!?:").rstrip()
            claim = _SENTENCE_BREAK.split(text)[-1]
        if int(match.group(1)) == marker:
            segments.append(claim)
        previous_end = match.end()
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
    marker states. A citation with one cell or none, or whose cells the claim
    never states, is returned unchanged -- a citation is never hidden."""
    if len(citation.cells) < 2:
        return citation
    figures = stated_figures(_MARKER.sub(" ", claim_text(prose, citation.marker)))
    by_key = {(cell.sid, cell.cell_index): cell for cell in cells}
    kept = tuple(
        key for key in citation.cells if key in by_key and is_stated(by_key[key], figures)
    )
    return dataclasses.replace(citation, cells=kept) if kept else citation
