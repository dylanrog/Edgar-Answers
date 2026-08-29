from __future__ import annotations

import unicodedata

# Design §6.3: curly quotes -> straight, en/em dashes -> hyphen. LLMs
# routinely "improve" typography when quoting, and EDGAR HTML is full of
# both forms; without this a verbatim quote fails an exact substring match
# for reasons that have nothing to do with faithfulness.
_REPLACEMENTS = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "–": "-",
    "—": "-",
    "−": "-",
}

# Financial tables read "$ 383,285" and "(3) %"; a model quoting them writes
# "$383,285" and "(3)%". Dropping the space on exactly these boundaries makes
# the two forms match. Deliberately narrow -- a general "ignore whitespace"
# rule would let a quote match across word boundaries that never existed in
# the source, and verification's whole value is that it is strict.
_NO_SPACE_AFTER = "$€£#("
_NO_SPACE_BEFORE = ")%"


def normalize(text: str) -> tuple[str, list[int]]:
    """Normalize for citation matching, returning a normalized->original offset map.

    `offset_map[i]` is the index in `text` of the character that produced
    `normalized[i]`. A character may produce zero characters (a whitespace
    run after the first) or several (NFKC expands ligatures, casefold expands
    the eszett), so the map is built per source character rather than derived
    from lengths.

    NFKC is applied per character, not to the whole string. That loses
    composition across character boundaries (an "e" followed by a combining
    acute stays two characters instead of becoming a single "e-acute"), which
    is the price of an exact offset map. It is safe here because both the
    quote and the chunk text pass through this same function: a sequence that
    fails to compose fails to compose identically on both sides, so the match
    still succeeds.
    """
    out: list[str] = []
    offsets: list[int] = []
    in_space_run = False
    for index, char in enumerate(text):
        if char.isspace():
            if not in_space_run:
                out.append(" ")
                offsets.append(index)
                in_space_run = True
            continue
        in_space_run = False
        if out and out[-1] == " " and (
            (len(out) >= 2 and out[-2] in _NO_SPACE_AFTER) or char in _NO_SPACE_BEFORE
        ):
            # Retract the space we already emitted, dropping its offset with
            # it so the map stays one entry per emitted character.
            out.pop()
            offsets.pop()
        piece = unicodedata.normalize("NFKC", _REPLACEMENTS.get(char, char)).casefold()
        for produced in piece:
            out.append(produced)
            offsets.append(index)
    return "".join(out), offsets
