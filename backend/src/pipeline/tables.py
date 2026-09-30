"""Column binding: parse one <table> into numeric cell records (spec 2026-09-29 §4).

Read-only on the DOM -- the canonicalizer's sentences and viewer HTML must
come out byte-identical with or without this module (spec §4.1). Every rule
that cannot decide stores NULL rather than a guess (spec §4.2): a NULL label
makes a number unverifiable, a wrong one would make a wrong answer look
verified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

# Spec §4.3 rule 2. Applied after removing whitespace, '$' and ','.
_NUMBER = re.compile(r"^\(?-?\d+(?:\.\d+)?\)?%?$")
_NIL = {"—", "–", "-", "−"}
_FILLER = {"", "$", "%", ")", "(", "$("}
_YEAR = re.compile(r"^\d{4}$")
_SCALE = re.compile(r"in\s+(thousands|millions|billions)", re.IGNORECASE)
_SCALE_PHRASE = re.compile(
    r"\(?\s*(?:dollars\s+|amounts\s+)?in\s+(?:thousands|millions|billions)[^)]*\)?",
    re.IGNORECASE,
)
_EXCEPT = re.compile(r"except\s+(?:per[\s-]share|percentages)", re.IGNORECASE)
_SCALES = {
    "thousands": Decimal(1000),
    "millions": Decimal(1000000),
    "billions": Decimal(1000000000),
}
SCALE_NAMES = {value: name for name, value in _SCALES.items()}
LABEL_JOIN = " › "


@dataclass(frozen=True)
class TableInfo:
    table_id: int
    caption: str | None
    scale: Decimal | None
    splittable: bool


@dataclass(frozen=True)
class Cell:
    sid: int
    col: int
    cell_index: int
    char_start: int | None
    char_end: int | None
    table_id: int
    raw: str
    value: Decimal | None
    kind: str  # 'number' | 'percent' | 'nil'
    row_label: str | None
    column_label: str | None
    scale_applies: bool


@dataclass
class _GridCell:
    index: int  # position among the row's td/th == the browser's tr.cells[i]
    start: int  # half-open grid range [start, end)
    end: int
    text: str
    kind: str  # 'number' | 'percent' | 'nil' | 'filler' | 'text'
    value: Decimal | None = None
    year: bool = False


@dataclass
class _Row:
    sid: int
    sentence_text: str
    cells: list[_GridCell]


def cell_text(td) -> str:
    return " ".join(td.get_text(" ", strip=True).split())


def _colspan(td) -> int:
    try:
        return max(1, int(td.get("colspan", 1)))
    except (TypeError, ValueError):
        return 1


def _classify(text: str) -> tuple[str, Decimal | None, bool]:
    """(kind, value, is_bare_year) for one cell's text."""
    squeezed = "".join(text.split()).replace("$", "").replace(",", "")
    if squeezed in _FILLER:
        return "filler", None, False
    if squeezed in _NIL:
        return "nil", None, False
    if not _NUMBER.match(squeezed):
        return "text", None, False
    negative = "(" in squeezed or ")" in squeezed or squeezed.startswith("-")
    digits = squeezed.strip("()%").lstrip("-")
    value = Decimal(digits)
    kind = "percent" if squeezed.endswith("%") else "number"
    bare = text.strip()
    year = bool(_YEAR.match(bare)) and 1990 <= int(bare) <= 2100
    return kind, (-value if negative else value), year


def _grid_row(sid: int, sentence_text: str, tr) -> _Row:
    cells: list[_GridCell] = []
    cursor = 0
    for index, td in enumerate(tr.find_all(["td", "th"], recursive=False)):
        span = _colspan(td)
        text = cell_text(td)
        kind, value, year = _classify(text)
        cells.append(_GridCell(index, cursor, cursor + span, text, kind, value, year))
        cursor += span
    # A lone '%' or ')' cell belongs to the number on its left (rule 2).
    for left, right in pairwise(cells):
        if left.kind == "number" and right.kind == "filler":
            if right.text == "%":
                left.kind = "percent"
            elif right.text == ")" and left.value is not None and left.value > 0:
                left.value = -left.value
    return _Row(sid, sentence_text, cells)


def _char_spans(row: _Row) -> dict[int, tuple[int, int]] | None:
    """Each cell's [start, end) inside the row sentence, or None if the
    cell-by-cell join does not reproduce the sentence exactly (spec §5.7)."""
    spans: dict[int, tuple[int, int]] = {}
    pieces: list[str] = []
    cursor = 0
    for cell in row.cells:
        if not cell.text:
            continue
        if pieces:
            cursor += 1
        spans[cell.index] = (cursor, cursor + len(cell.text))
        pieces.append(cell.text)
        cursor += len(cell.text)
    if " ".join(pieces) != row.sentence_text:
        return None
    return spans


def _is_year_row(row: _Row) -> bool:
    numeric = [c for c in row.cells if c.kind in ("number", "percent")]
    return bool(numeric) and all(c.year for c in numeric)


def parse_table(
    table_id: int, rows: list[tuple[int, str, object]], caption: str | None
) -> tuple[TableInfo, list[Cell]]:
    """Parse one table. `rows` is (sid, sentence text, <tr>) for every row that
    produced a sentence, in document order (spacer rows never do)."""
    grid = [_grid_row(sid, text, tr) for sid, text, tr in rows]
    year_rows = {id(r) for r in grid if _is_year_row(r)}

    # Rule 3: label columns end where the first value starts. Year-only
    # header rows are ignored here, or '2025' would claim to be data.
    starts = [
        c.start
        for r in grid
        if id(r) not in year_rows
        for c in r.cells
        if c.kind in ("number", "percent", "nil")
    ]
    if not starts:
        return TableInfo(table_id, caption, _scale(grid, caption)[0], False), []
    label_end = min(starts)

    scale, exempt_per_share = _scale(grid, caption)
    band: list[_Row] = []
    data_since_band = False
    group: str | None = None
    splittable = True
    out: list[Cell] = []

    for row in grid:
        values = [
            c for c in row.cells
            if c.start >= label_end and c.kind in ("number", "percent", "nil")
        ]
        label = " ".join(
            c.text for c in row.cells if c.end <= label_end and c.kind == "text" and c.text
        ) or None
        in_value_columns = any(c.text for c in row.cells if c.end > label_end)

        if values and id(row) not in year_rows:
            data_since_band = True
            if not band:
                splittable = False
            row_label = LABEL_JOIN.join(p for p in (group, label) if p) or None
            spans = _char_spans(row)
            for c in values:
                span = spans.get(c.index) if spans is not None else None
                per_share = bool(row_label) and "per share" in row_label.lower()
                out.append(
                    Cell(
                        sid=row.sid,
                        col=c.start,
                        cell_index=c.index,
                        char_start=span[0] if span else None,
                        char_end=span[1] if span else None,
                        table_id=table_id,
                        raw=c.text,
                        value=c.value,
                        kind=c.kind,
                        row_label=row_label,
                        column_label=_column_label(band, c),
                        scale_applies=c.kind == "number" and not (exempt_per_share and per_share),
                    )
                )
        elif in_value_columns:
            # Rule 5: a header row after data starts a new band (Apple's
            # mid-table period change) and clears the group.
            if data_since_band:
                band, data_since_band, group = [], False, None
            band.append(row)
        elif label:
            group = label

    return TableInfo(table_id, caption, scale, splittable), out


def _column_label(band: list[_Row], cell: _GridCell) -> str | None:
    parts: list[str] = []
    for row in band:
        over = [
            c for c in row.cells
            if c.text and c.start < cell.end and cell.start < c.end
        ]
        if len(over) > 1:
            return None  # two headers claim this number: NULL, never a guess
        if over:
            text = " ".join(_SCALE_PHRASE.sub(" ", over[0].text).split())
            if text:
                parts.append(text)
    return LABEL_JOIN.join(parts) or None


def _scale(grid: list[_Row], caption: str | None) -> tuple[Decimal | None, bool]:
    """Rule 6: header rows first, then the caption. First match wins."""
    header_texts = [
        " ".join(c.text for c in r.cells if c.text)
        for r in grid
        if not any(c.kind in ("number", "percent", "nil") for c in r.cells) or _is_year_row(r)
    ]
    for text in [*header_texts, caption or ""]:
        match = _SCALE.search(text)
        if match:
            return _SCALES[match.group(1).lower()], bool(_EXCEPT.search(text))
    return None, False
