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
from dataclasses import dataclass
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


def claim_sides(prose: str, marker: int) -> tuple[str, str] | None:
    """(before, after): the words on each side of every `[marker]`, or None
    when the marker is not in the prose.

    Before is the text back to the previous marker or sentence break; after
    runs to the next marker or sentence end. Which side a marker cites depends
    on style -- "Revenue was $115.2 billion [1]." vs "comparison: [1] Revenue
    was $115.2 billion." -- and "X. [1] Y. [2]" reads either way, so callers
    use both. Words a sentence-opening marker claimed after it are not
    offered again as the next marker's before side.
    """
    befores: list[str] = []
    afters: list[str] = []
    groups = _groups(prose)
    previous_end = 0
    claimed_until = 0  # words up to here belong to an earlier group
    for i, (start, end, markers) in enumerate(groups):
        lead = prose[previous_end:start].rstrip(" \t")
        starts_sentence = not lead.strip() or lead[-1] in ".!?:\n"
        before = _before(prose, start, max(previous_end, claimed_until))
        after = _after(prose, end, groups[i + 1] if i + 1 < len(groups) else None)
        if marker in markers:
            befores.append(before)
            afters.append(after)
        claimed_until = end + len(after) if starts_sentence and after.strip() else end
        previous_end = end
    if not befores:
        return None
    return " ".join(befores), " ".join(afters)


# The unit written right after a figure: "$115.2 billion", "$115.2B",
# "115.2bn", "$115.2-billion", "$(1,234) million", "47.5%".
_UNIT = re.compile(
    r"\)?[ \t]*-?[ \t]*(?:(?P<trillion>(?i:trillions?)|T)"
    r"|(?P<billion>(?i:billions?|bn)|B|b)|(?P<million>(?i:millions?|mn)|M)"
    r"|(?P<percent>%|(?i:percent|per cent)))(?![A-Za-z])"
)
# Between the ends of a range: "$115-116 billion", "$115 to $116 billion".
_RANGE = re.compile(r"[ \t]*(?:-|–|to)[ \t]*\$?[ \t]*$")
# How many of a table's units make one of the prose unit: a millions table's
# 115,186 is 115.186 billion or 0.115186 trillion; a thousands table's 115,186
# is 115.186 million. The table's own scale is not consulted.
_STEP = {"billion": Decimal(1000), "million": Decimal(1000), "trillion": Decimal(1000000)}


@dataclass(frozen=True)
class Figure:
    """A number the prose states, with the unit written right after it."""

    value: Decimal  # absolute: "(1,234)" and "-1,234" both read 1234
    places: int  # decimals as written: "115.2" -> 1
    unit: str  # "trillion" | "billion" | "million" | "percent" | ""

    @property
    def tolerance(self) -> Decimal:
        """How far a value can sit from this figure and still be what it
        rounds or truncates: a whole unit for "$22 billion" (22.1, 22.9),
        half a unit beyond that."""
        return max(Decimal("0.5"), Decimal(1).scaleb(-self.places))


def stated_figures(text: str) -> list[Figure]:
    found = []
    for match in _FIGURE.finditer(text):
        value = Decimal(match.group().replace(",", ""))
        unit_match = _UNIT.match(text, match.end())
        unit = (unit_match.lastgroup or "") if unit_match else ""
        found.append([match.start(), match.end(), value, unit])
    # A range's first end takes the unit of its second: "$115-116 billion".
    for first, second in zip(found, found[1:], strict=False):
        if not first[3] and second[3] and _RANGE.match(text[first[1] : second[0]]):
            first[3] = second[3]
    return [
        Figure(value, max(0, -value.as_tuple().exponent), unit)
        for _, _, value, unit in found
    ]


def _rounded(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)


def _scaled(cell: Cell, figure: Figure) -> Decimal | None:
    """The cell's value in the prose figure's unit, when the figure names a
    unit above the table's (see _STEP); None when it doesn't."""
    if cell.kind != "number" or cell.value is None or figure.unit not in _STEP:
        return None
    return abs(cell.value) / _STEP[figure.unit]


def is_stated(cell: Cell, figures: list[Figure]) -> bool:
    """The cell's figure is among `figures`.

    A number cell matches a non-percent figure stated with its exact digits
    ("115,186", "$115,186 million"), or one unit up -- billion/bn/B/b or
    trillion for a millions table, million for a thousands table -- rounded
    to the decimals the prose used ("$115 billion", "$115.2B", "115.186
    billion", "$0.1152 trillion"). A bare "115.2" with no unit matches
    nothing scaled. A percent cell matches only a figure written as a
    percent, rounded to the prose's decimals. A number never matches a
    percent, nor a percent a number.
    """
    if cell.value is None:
        return False
    value = abs(cell.value)
    for figure in figures:
        if (cell.kind == "percent") != (figure.unit == "percent"):
            continue
        if figure.value == value:
            return True
        if cell.kind == "percent" and figure.value == _rounded(value, figure.places):
            return True
        scaled = _scaled(cell, figure)
        if scaled is not None and figure.value == _rounded(scaled, figure.places):
            return True
    return False


def _near_miss(cell: Cell, figures: list[Figure]) -> bool:
    """A figure within rounding distance (`Figure.tolerance`) of the cell that
    still didn't match it, so the claim may well be about this cell: "about
    $22 billion" beside 22.1 in a billions table, "about $6.1" beside a 6.08
    per-share figure, "$47 billion" beside 47,525, "47%" beside 47.5%.
    Compared with the cell's own value whatever the figure's unit, and with
    its scaled value when the unit is one up."""
    if cell.value is None:
        return False
    value = abs(cell.value)
    for figure in figures:
        if (cell.kind == "percent") != (figure.unit == "percent"):
            continue
        if abs(figure.value - value) <= figure.tolerance:
            return True
        scaled = _scaled(cell, figure)
        if scaled is not None and abs(figure.value - scaled) <= figure.tolerance:
            return True
    return False


def narrow_cells(
    citation: VerifiedCitation, cells: list[Cell], prose: str
) -> VerifiedCitation:
    """Keep only the cited cells whose figure the claim around the citation's
    marker states, on either side of the marker (the union: a claim that
    reads ambiguously keeps both candidates rather than guessing one).

    Fails closed -- the citation is returned unchanged -- when it cites fewer
    than two cells, when its marker is not in the prose, when no cited figure
    is stated, or when a stated figure nearly matches a cited cell it did not
    match. Each row (sid) is narrowed on its own; a row none of whose cells
    is stated keeps all of them. Never touches `verified`, `sids` or `quote`.
    """
    if len(citation.cells) < 2:
        return citation
    sides = claim_sides(prose, citation.marker)
    if sides is None:
        return citation
    figures = [f for side in sides for f in stated_figures(_MARKER.sub(" ", side))]
    by_key = {(cell.sid, cell.cell_index): cell for cell in cells}
    stated = {
        key for key in citation.cells if key in by_key and is_stated(by_key[key], figures)
    }
    if not stated:
        return citation
    if any(
        _near_miss(by_key[key], figures)
        for key in citation.cells
        if key in by_key and key not in stated
    ):
        return citation
    rows_stated = {sid for sid, _ in stated}
    kept = tuple(
        key for key in citation.cells if key in stated or key[0] not in rows_stated
    )
    return dataclasses.replace(citation, cells=kept)
