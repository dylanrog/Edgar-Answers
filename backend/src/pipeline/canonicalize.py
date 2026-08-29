from __future__ import annotations

from dataclasses import dataclass
from itertools import count

import pysbd
from bs4 import BeautifulSoup

from .sections import SectionTracker

_STRIP_TAGS = ["script", "style", "iframe", "object", "embed", "ix:header", "ix:hidden"]
_BLOCK_TAGS = ["p", "li", "div"]

# Declarations whose *only* job is colour. 'border-bottom: 1px solid #000'
# deliberately does not match: it carries geometry too, and dropping it would
# lose the rule line from every financial table.
_COLOR_PROPERTIES = ("color", "background", "background-color")


@dataclass(frozen=True)
class Sentence:
    sid: int
    section: str
    text: str
    char_start: int
    char_end: int
    # NULL for prose. Last field with a default so existing positional
    # constructions keep working.
    table_id: int | None = None


@dataclass(frozen=True)
class CanonicalFiling:
    canonical_text: str
    sentences: list[Sentence]
    viewer_html: str


def strip_color_declarations(style: str) -> str:
    """Remove colour-bearing declarations from one inline style attribute.

    EDGAR filings arrive with colour baked into thousands of inline styles
    (one cached AMZN 10-K: 9,316 style attributes, 6,574 colour declarations,
    nearly all '#000000'). Those render as invisible text on a dark viewer.
    Layout properties are kept -- EDGAR tables rely on width, alignment and
    borders for their geometry, so a blanket strip mangles them.
    """
    kept = []
    for declaration in style.split(";"):
        if not declaration.strip():
            continue
        prop, _, _value = declaration.partition(":")
        if prop.strip().lower() in _COLOR_PROPERTIES:
            continue
        kept.append(declaration.strip())
    return "; ".join(kept)


def canonicalize(raw_html: str, form_type: str) -> CanonicalFiling:
    """One DOM traversal producing aligned canonical text and sid-annotated viewer HTML."""
    soup = BeautifulSoup(raw_html, "lxml")
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()
    for tag in soup.find_all(lambda t: t.name is not None and t.name.startswith("ix:")):
        tag.unwrap()
    for el in soup.find_all(True):
        for attr in [a for a in el.attrs if a.lower().startswith("on")]:
            del el.attrs[attr]
        style = el.attrs.get("style")
        if style is not None:
            cleaned = strip_color_declarations(style)
            if cleaned:
                el["style"] = cleaned
            else:
                del el.attrs["style"]

    segmenter = pysbd.Segmenter(language="en", clean=False, char_span=True)
    tracker = SectionTracker(form_type)
    sentences: list[Sentence] = []
    cursor = 0

    body = soup.body if soup.body is not None else soup
    for kind, payload, table_id in list(_iter_units(body, count(1))):
        if kind == "row":
            text = " ".join(payload.get_text(" ", strip=True).split())
            if not text:
                continue  # spacer row: EDGAR uses these purely for layout
            sid = len(sentences)
            # A row is not prose, so it is never segmented, and it does not
            # feed the section tracker -- a row like "Item 7 12,345" would
            # otherwise be read as a heading.
            sentences.append(
                Sentence(sid, tracker.current, text, cursor, cursor + len(text), table_id)
            )
            cursor += len(text) + 1
            payload["data-sid"] = str(sid)
            continue

        nodes = [payload] if kind == "block" else payload
        text = _unit_text(nodes)
        if not text:
            continue
        section = tracker.update(text)
        block_sentences: list[Sentence] = []
        for span in segmenter.segment(text):
            sent_text = span.sent.strip()
            if not sent_text:
                continue
            start = cursor
            end = start + len(sent_text)
            sid = len(sentences) + len(block_sentences)
            block_sentences.append(Sentence(sid, section, sent_text, start, end))
            cursor = end + 1  # sentences join with "\n" in canonical_text
        if block_sentences:
            if kind == "block":
                _rewrite_block(soup, payload, block_sentences)
            else:
                _rewrite_run(soup, payload, block_sentences)
            sentences.extend(block_sentences)

    canonical_text = "\n".join(s.text for s in sentences)
    viewer_html = "".join(str(child) for child in body.children)
    return CanonicalFiling(canonical_text, sentences, viewer_html)


def _is_leaf(el) -> bool:
    """A block with no block-level child and no table inside it."""
    return el.find(_BLOCK_TAGS) is None and el.find("table") is None


def _innermost_rows(table):
    """Rows that contain no nested table. An outer row wrapping a nested table
    is a container: emitting it too would index the inner text twice."""
    for tr in table.find_all("tr"):
        if tr.find("table") is None:
            yield tr


def _iter_units(node, table_ids):
    """Yield (kind, payload, table_id) in document order.

    kind is 'block' (a leaf p/li/div, payload is the element), 'run' (a run of
    consecutive inline nodes directly under a container, payload is the list),
    or 'row' (payload is a <tr>). Runs exist so that loose text sitting beside
    a table inside the same div is not lost -- that div is a container, so
    without runs its text would never be visited.
    """
    run: list = []
    for child in node.children:
        name = getattr(child, "name", None)
        if name == "table":
            if run:
                yield ("run", run, None)
                run = []
            table_id = next(table_ids)
            for tr in _innermost_rows(child):
                yield ("row", tr, table_id)
        elif name in _BLOCK_TAGS:
            if run:
                yield ("run", run, None)
                run = []
            if _is_leaf(child):
                yield ("block", child, None)
            else:
                yield from _iter_units(child, table_ids)
        elif name is not None and (
            child.find(_BLOCK_TAGS) is not None or child.find("table") is not None
        ):
            # A non-block wrapper (center, font, section...) holding structure.
            # Descend so blocks and tables anywhere in the tree are still found.
            if run:
                yield ("run", run, None)
                run = []
            yield from _iter_units(child, table_ids)
        else:
            run.append(child)
    if run:
        yield ("run", run, None)


def _unit_text(nodes) -> str:
    parts = []
    for node in nodes:
        if getattr(node, "name", None) is None:
            parts.append(str(node))
        else:
            parts.append(node.get_text(" ", strip=True))
    return " ".join(" ".join(parts).split())


def _rewrite_block(soup: BeautifulSoup, block, block_sentences: list[Sentence]) -> None:
    """Replace block content with sid-tagged spans. Inline formatting inside a
    paragraph is flattened in v1; block structure and tables are preserved."""
    block.clear()
    for i, s in enumerate(block_sentences):
        span = soup.new_tag("span")
        span["data-sid"] = str(s.sid)
        span.string = s.text
        block.append(span)
        if i < len(block_sentences) - 1:
            block.append(" ")


def _rewrite_run(soup, run: list, run_sentences: list[Sentence]) -> None:
    """Replace a run of inline nodes with sid-tagged spans, in place.

    Spans are inserted at the run's original position and the original nodes
    removed, so document order -- and therefore sid order -- is preserved
    around any sibling table.
    """
    anchor = run[0]
    for i, s in enumerate(run_sentences):
        span = soup.new_tag("span")
        span["data-sid"] = str(s.sid)
        span.string = s.text
        anchor.insert_before(span)
        if i < len(run_sentences) - 1:
            anchor.insert_before(" ")
    for node in run:
        node.extract()
