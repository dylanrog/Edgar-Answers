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
from difflib import SequenceMatcher

from pipeline.tables import Cell

from .normalize import normalize
from .verify import VerifiedCitation, find_quote

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
    return [figure for _, _, figure in _located_figures(text)]


def _located_figures(text: str) -> list[tuple[int, int, Figure]]:
    """stated_figures, each with where its digits start and end in `text`."""
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
        (start, end, Figure(value, max(0, -value.as_tuple().exponent), unit))
        for start, end, value, unit in found
        if not _is_year(text, start, end, unit)
    ]


def _is_year(text: str, start: int, end: int, unit: str) -> bool:
    """A bare year ("fiscal 2025") is not a figure: it would coincide with a
    2,025 cell. A real figure that size carries a comma or a "$" or a unit."""
    token = text[start:end]
    return (
        not unit
        and len(token) == 4
        and token.isdigit()
        and 1900 <= int(token) <= 2100
        and not text[:start].rstrip().endswith("$")
    )


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

    A claim that states no figure at all ("[3] The Data Center segment
    includes both computing and networking products"; a bare year doesn't
    count) marks no cells: every cited row gets the plain whole-row
    highlight, since emphasizing its figures would point at numbers the
    answer never gives.

    Fails closed -- the citation is returned unchanged -- when it cites fewer
    than two cells, when its marker is not in the prose, when the claim states
    figures but none is a cited one, or when a stated figure nearly matches a
    cited cell it did not match. Otherwise only the stated cells are kept: a
    cited row none of whose figures the claim states loses its cell emphasis,
    and the viewer falls back to highlighting that row whole -- accurate,
    since the quote does cover it. Never touches `verified`, `sids` or `quote`.
    """
    if len(citation.cells) < 2:
        return citation
    sides = claim_sides(prose, citation.marker)
    if sides is None:
        return citation
    figures = [f for side in sides for f in stated_figures(_MARKER.sub(" ", side))]
    if not figures:
        return dataclasses.replace(citation, cells=())
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
    kept = tuple(key for key in citation.cells if key in stated)
    return dataclasses.replace(citation, cells=kept)


# --- The repair gate (design §6.3 step 5) -----------------------------------
#
# Stricter than narrowing on purpose. Narrowing only chooses which already
# verified figures to emphasize; this decides whether a *second* quote may turn
# a failed citation green, so every doubt fails closed: unit, table scale,
# period and sign must all agree, and rounding may only drop decimals.

_UNIT_VALUE = {
    "million": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9,
    "trillion": Decimal(10) ** 12,
}
# What a negative figure is written after: "(1,234)", "-1,234", "$(1,234)", "−1,234".
_NEGATIVE_LEAD = re.compile(r"[(\-−–]\s*\$?\s*$")
_FULL_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
# "FY24", "FY'24", "fiscal 24", "fiscal year 24".
_SHORT_YEAR = re.compile(r"\b(?:FY|fiscal(?:\s+year)?)\s*'?(\d{2})\b", re.IGNORECASE)


def _significant(figure: Figure) -> bool:
    return bool(figure.unit or figure.places or figure.value >= 1000)


def significant_figures(text: str) -> list[Figure]:
    """The figures `text` states that can pin a claim to a value: written with
    a unit, with decimals, or at least 1,000. A bare small integer ("Q2",
    "Item 7", "Note 4", "3 segments") is not one, and stated_figures already
    drops bare years. Markers are removed first so "[1]" is never a figure."""
    return [f for f in stated_figures(_MARKER.sub(" ", text)) if _significant(f)]


# Direction words: a loss, a fall, an outflow or a cost. The claim and the
# quote must agree on whether they use one *anywhere* (not just near the
# figure), so "net income was $1.2 billion" is never stated by "Net loss for
# fiscal year 2025 was $1.2 billion", nor cash "provided by" by cash "used in",
# nor "other income" by "Other income (expense), net".
_DOWN = re.compile(
    r"\b(?:loss(?:es)?|decreas(?:e|ed|es|ing)|declin(?:e|ed|es|ing)|fell"
    r"|fall(?:s|ing)?|lower|negative|down|used\s+in|expens(?:e|es))\b",
    re.IGNORECASE,
)


def _positive_significant_figures(text: str) -> list[Figure]:
    """significant_figures minus any written as a negative (after "(", "-" or
    "−"): a repair never rests on a negative figure."""
    text = _MARKER.sub(" ", text)
    return [
        figure
        for start, _, figure in _located_figures(text)
        if _significant(figure) and not _NEGATIVE_LEAD.search(text[:start])
    ]


# Period type. A quarter claim needs a quarter's figure, a year claim a year's.
_QUARTER_CLAIM = re.compile(
    r"\bQ[1-4]\b|\b(?:first|second|third|fourth)\s+quarter\b|\bthree\s+months\b"
    r"|\bquarter(?:ly)?\b|\b13\s+weeks\b",
    re.IGNORECASE,
)
_YEAR_CLAIM = re.compile(
    r"\bfiscal\s+(?:year\s+)?(?:19|20)?\d{2}\b|\bFY\s*'?\d{2,4}\b|\bfiscal\s+year\b"
    r"|\bfull[\s-]year\b|\bannual(?:ly)?\b|\byear\s+ended\b|\btwelve\s+months\b",
    re.IGNORECASE,
)
# A span shorter than a year, in a column label or a quote.
_PART_YEAR = re.compile(
    r"\b(?:three|six|nine|3|6|9)\s+months\b|\bquarter(?:ly)?\b|\bQ[1-4]\b"
    r"|\b(?:13|26|39)\s+weeks\b",
    re.IGNORECASE,
)
_THREE_MONTHS = re.compile(
    r"\b(?:three|3)\s+months\b|\bquarter\b|\bQ[1-4]\b|\b13\s+weeks\b", re.IGNORECASE
)
# A span other than a quarter: in a label it rules a quarter claim out.
_NOT_QUARTER = re.compile(
    r"\b(?:six|nine|twelve|6|9|12)\s+months\b|\byear\b|\bannual\b"
    r"|\b(?:26|39|52|53)\s+weeks\b",
    re.IGNORECASE,
)


def period_kind(text: str) -> str | None:
    """"quarter" when the claim names a quarter (Q2, second quarter, three
    months), else "year" when it names a fiscal or full year ("fiscal 2025",
    "FY24", "full-year", "annual"), else None."""
    if _QUARTER_CLAIM.search(text):
        return "quarter"
    if _YEAR_CLAIM.search(text):
        return "year"
    return None


def _span_fits(label: str, kind: str | None) -> bool:
    """A column label or a prose quote covers the claim's kind of period. A
    quarter needs three months / a quarter and nothing longer; a year needs no
    shorter span ("Three/Six/Nine Months Ended" fail a full-year claim)."""
    if kind == "quarter":
        return bool(_THREE_MONTHS.search(label)) and not _NOT_QUARTER.search(label)
    if kind == "year":
        return not _PART_YEAR.search(label)
    return True


def claim_years(text: str) -> set[str]:
    """The fiscal years a claim names: "2025", and "FY24" / "fiscal 24" as 2024."""
    years = set(_FULL_YEAR.findall(text))
    years |= {f"20{yy}" for yy in _SHORT_YEAR.findall(text)}
    return years


def _agrees(value: Decimal, figure: Figure) -> bool:
    """`value` (from the source) is `figure` (from the claim): equal, or
    written with more decimals and rounding to it without changing the whole
    number -- 391.0 for 391, 115.186 or 115.19 for 115.2; never 3.5 for 4,
    never 115.2 for 115.19."""
    if value == figure.value:
        return True
    places = max(0, -value.as_tuple().exponent)
    return (
        places > figure.places
        and int(value) == int(figure.value)
        and _rounded(value, figure.places) == figure.value
    )


def _negative_cell(cell: Cell) -> bool:
    raw = cell.raw.strip()
    return (
        (cell.value is not None and cell.value < 0)
        or "(" in raw
        or raw[:1] in ("-", "−", "–")
    )


def _in_period(cell: Cell, years: set[str]) -> bool:
    if not years:
        return True
    label = cell.column_label or ""
    return any(re.search(rf"(?<!\d){year}(?!\d)", label) for year in years)


def _cell_supports(
    cell: Cell,
    figure: Figure,
    scales: dict[int, Decimal | None],
    years: set[str],
    kind: str | None,
) -> bool:
    """A quoted table cell states this claim figure, for the repair gate.

    Sign: a negative cell never counts. Period: when the claim names years,
    the cell's column label must contain one of them (no label: no), and
    when it names a quarter or a year, the label must cover that span
    (`_span_fits`). Kind:
    percent only for percent. Scale: a figure with a unit needs a cell the
    table's scale applies to, in a table scaled in that unit (exact) or one
    unit below (115,186 in millions for "$115.2 billion", 115,186 in thousands
    for "$115.2 million"); an unknown scale never counts. A figure with no
    unit ("$6.08") only matches a cell the scale does not apply to (per share).
    """
    if cell.value is None or _negative_cell(cell) or not _in_period(cell, years):
        return False
    if kind is not None and not _span_fits(cell.column_label or "", kind):
        return False
    if cell.kind == "percent":
        return figure.unit == "percent" and _agrees(cell.value, figure)
    if cell.kind != "number" or figure.unit == "percent":
        return False
    if not figure.unit:
        return not cell.scale_applies and _agrees(cell.value, figure)
    unit = _UNIT_VALUE[figure.unit]
    scale = scales.get(cell.table_id)
    if not cell.scale_applies or scale is None:
        return False
    if scale == unit:
        return _agrees(cell.value, figure)
    if scale * 1000 == unit:
        return _agrees(cell.value / 1000, figure)
    return False


def quote_states(
    quote: str,
    quote_cells: list[Cell],
    claim: str,
    scales: dict[int, Decimal | None],
) -> bool:
    """The repaired quote states *every* significant figure the claim states
    (design §6.3 step 5), each checked on its own -- so two spellings of one
    value ("$115,186 million ($115.2 billion)") are both met by its one cell,
    and figures spread over quoted rows are each met by their own row.

    A figure is met by a cell the quote covers (`_cell_supports`: sign,
    period, period type, kind and table scale) or by a figure written in the
    quote text (exactly the same unit, not written as a negative, not one of
    the quoted cells' own values, `_agrees`). The claim and the quote must
    agree on direction: a direction word ("loss", "decreased", "used in",
    "expense", ...) anywhere in one needs one anywhere in the other. A prose
    figure also needs the quote's period to fit the claim's: when the claim
    names years, the quote must name at least one of them and no other (a
    quote naming no year can't say which year its figure is for), and its
    span must fit a quarter or year claim. A claim with no significant
    figure is never supported. `scales` maps the chunk's table ids to their
    scale (None when unknown)."""
    figures = significant_figures(claim)
    if not figures or bool(_DOWN.search(claim)) != bool(_DOWN.search(quote)):
        return False
    years = claim_years(claim)
    kind = period_kind(claim)
    # A figure that is one of the quoted cells is judged as that cell only, so
    # the text path can't bypass the cell's scale and period checks.
    cell_values = {abs(c.value) for c in quote_cells if c.value is not None}
    quote_years = claim_years(quote)
    prose_fits = (
        not years or (bool(quote_years) and quote_years <= years)
    ) and _span_fits(quote, kind)
    texts = (
        [q for q in _positive_significant_figures(quote) if q.value not in cell_values]
        if prose_fits
        else []
    )

    def stated(figure: Figure) -> bool:
        if any(_cell_supports(cell, figure, scales, years, kind) for cell in quote_cells):
            return True
        return any(q.unit == figure.unit and _agrees(q.value, figure) for q in texts)

    return all(stated(figure) for figure in figures)


# --- The transcription guard (design §6.3 step 5, figure-free claims) -------
#
# A claim with no significant figure has nothing quote_states could check, so
# its repair is held to what first-pass verification ever promised it: that
# the quote is real text. The repair may fix how the model copied a passage,
# not find a new one, and not change what it says: most of the failed quote
# must already be in the repaired one, contiguously, and what either quote
# has outside that shared run must be short and carry no negation, direction
# or scope word -- the words that would make a wrong claim look supported.

TRANSCRIPTION_SHARE = 0.6
MIN_TRANSCRIPTION_CHARS = 20
# Words outside the shared run: the failed quote may lose a few (the garbled
# bit), the repair may add a few more (a caption's lead-in), no more.
MAX_ORIGINAL_LEFTOVER_WORDS = 3
MAX_REPAIRED_LEFTOVER_WORDS = 12
_WORD = re.compile(r"\w+")
# Negation, direction and scope: dropping or adding any of these changes what
# the quote says. On word boundaries, casefolded; "n't" anywhere.
_POLARITY = re.compile(
    r"n't|\b(?:not|no|non|never|nor|without|none|cannot|longer|only|except|unless"
    r"|increas\w*|rose|grew|higher|up|more|gain\w*|exclud\w*|includ\w*)\b",
    re.IGNORECASE,
)


def _boundary(text: str, i: int) -> bool:
    return i <= 0 or i >= len(text) or not (text[i - 1].isalnum() and text[i].isalnum())


def _shared_run(a: str, b: str) -> tuple[int, int, int]:
    """(start in a, start in b, length) of the longest common contiguous run,
    trimmed so it starts and ends on a word boundary in both strings -- a run
    ending on the "in" of "increased" must not count those letters."""
    match = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    i, j, n = match.a, match.b, match.size
    while n > 0 and not (_boundary(a, i) and _boundary(b, j)):
        i, j, n = i + 1, j + 1, n - 1
    while n > 0 and not (_boundary(a, i + n) and _boundary(b, j + n)):
        n -= 1
    return i, j, n


def _transcription_form(text: str) -> str:
    """normalize() (casefold, curly quotes and dashes straightened), with a
    hyphen read as a space and whitespace collapsed: "all-time" and "all
    time" are one transcription."""
    return " ".join(normalize(text)[0].replace("-", " ").split())


# What a clause ends with, for the boundary check on a side the repair drops.
_CLAUSE_END = ".:;?!"
# Where a repair that drops lead words may start: after a sentence end only.
# Not after ":" or ";" -- the dropped words may be a label ("Data Center:
# revenue was driven by ..."), and dropping it would swap the subject.
_LEAD_BOUNDARY = ".?!"


def _starts_a_clause(excerpt: str, start: int) -> bool:
    """excerpt[start:] begins the chunk or follows a sentence end or a newline
    (spaces between are ignored)."""
    before = excerpt[:start].rstrip(" \t")
    return not before or before[-1] in _LEAD_BOUNDARY + "\n"


def _ends_a_clause(excerpt: str, end: int) -> bool:
    """excerpt[:end] ends with a clause end, or what follows it (spaces
    ignored) is a clause end, a newline, or the end of the chunk."""
    if excerpt[:end].rstrip()[-1:] in tuple(_CLAUSE_END):
        return True
    after = excerpt[end:].lstrip(" \t")
    return not after or after[0] in _CLAUSE_END + "\n"


def transcription_matches(original: str, repaired: str, excerpt: str | None) -> bool:
    """The repair fixes how the failed quote was copied, and nothing else.

    Both quotes are put in `_transcription_form`. Their longest common
    contiguous run, trimmed to whole words, must be at least 60% of the
    failed quote and at least 20 characters. Outside the run:

    - edges only: on each side of the run (before, after) at most one of the
      two quotes has words left -- a repair trims or extends the failed
      quote at an edge, never replaces words on the same side ("Gaming
      revenue was ..." is never repaired to "Data Center revenue was ...");
    - the failed quote keeps at most 3 words and the repair at most 12;
    - neither leftover holds a negation, direction or scope word
      (`_POLARITY`, `_DOWN`);
    - every word the repair drops from the failed quote is a word of the
      excerpt (whole words, same form), so a trimmed tail can't take an
      invented "in China" with it. No excerpt: no repair;
    - on a side where the repair drops words, its edge must fall on a clause
      boundary of the excerpt (located as the verifier locates it): dropping
      a lead, it must start the chunk or follow ".", "?", "!" or a newline
      (not ":" or ";", where the dropped words may be a label: "Data Center:
      revenue was ..."); dropping a tail, it must end on ".", ":", ";", "?"
      or "!" or be followed by one, a newline or the chunk's end. Truncating
      "Data Center revenue was driven by…" to "revenue was driven by…"
      therefore never stands in for a failed "Gaming revenue was driven
      by…". A repair that can't be located fails.
    """
    if not excerpt:
        return False
    a = _transcription_form(original)
    b = _transcription_form(repaired)
    if not a or not b:
        return False
    i, j, n = _shared_run(a, b)
    if n < MIN_TRANSCRIPTION_CHARS or n < TRANSCRIPTION_SHARE * len(a):
        return False
    sides = ((a[:i], b[:j]), (a[i + n :], b[j + n :]))
    if any(_WORD.search(a_side) and _WORD.search(b_side) for a_side, b_side in sides):
        return False
    original_rest = a[:i] + " " + a[i + n :]
    repaired_rest = b[:j] + " " + b[j + n :]
    dropped = _WORD.findall(original_rest)
    if len(dropped) > MAX_ORIGINAL_LEFTOVER_WORDS:
        return False
    if len(_WORD.findall(repaired_rest)) > MAX_REPAIRED_LEFTOVER_WORDS:
        return False
    if any(
        _POLARITY.search(rest) or _DOWN.search(rest) for rest in (original_rest, repaired_rest)
    ):
        return False
    excerpt_words = set(_WORD.findall(_transcription_form(excerpt)))
    if not all(word in excerpt_words for word in dropped):
        return False
    drops_lead, drops_tail = (bool(_WORD.search(side)) for side, _ in sides)
    if not (drops_lead or drops_tail):
        return True
    span = find_quote(excerpt, repaired)
    if span is None:
        return False
    start, end = span
    return (not drops_lead or _starts_a_clause(excerpt, start)) and (
        not drops_tail or _ends_a_clause(excerpt, end)
    )


def figure_free_repair_accepted(original: str, repaired: str, excerpt: str | None) -> bool:
    """A figure-free claim's repair: a transcription fix of the failed quote
    against its excerpt (`transcription_matches`), stating no significant
    figure of its own -- a claim that gives no number must not end up
    verified against one."""
    return not significant_figures(repaired) and transcription_matches(
        original, repaired, excerpt
    )


def trim_before_first_figure(reply: str) -> str | None:
    """A figure-free repair cut short of its first number (design §6.3 step 5).

    Asked to fix a quote that stitched a table caption onto a row label, the
    repairer copies the caption on through the row's figures ("…markets we
    serve: Year Ended Jan 28, 2024 … Data Center $ 47,525"). A reply with no
    digit is returned unchanged. Otherwise it is cut at the last sentence end
    (".", "?" or "!" followed by whitespace) or colon before its first digit,
    keeping that punctuation, then stripped -- a verbatim prefix of the
    reply, so still exact text if the reply was. None when no such boundary
    exists or nothing is left: the repair fails closed.
    """
    first_digit = next((i for i, ch in enumerate(reply) if ch.isdigit()), None)
    if first_digit is None:
        return reply
    head = reply[:first_digit]
    cut = None
    for i, ch in enumerate(head):
        if ch == ":" or (ch in ".?!" and i + 1 < len(head) and head[i + 1].isspace()):
            cut = i + 1
    if cut is None:
        return None
    return reply[:cut].strip() or None
