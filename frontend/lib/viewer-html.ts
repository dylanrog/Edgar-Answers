/** An image source that can load from this origin: absolute http(s) or inline data. */
const LOADABLE_SRC = /^\s*(https?:|data:)/i;

/** Marks a table cell that holds a figure (globals.css keeps it unwrapped on phones). */
export const FIGURE_CELL_CLASS = "fig";

/**
 * A cell whose whole text is one figure: an amount, a percentage, a negative
 * in parentheses or with a dash, optionally with a currency sign. Words, dates
 * ("Jan 28, 2024") and anything longer are not figures and keep wrapping.
 */
const FIGURE_TEXT = /^[$€£¥(\-–—+\s]*\d[\d.,]*[\s)%]*$/;

export function isFigureText(text: string): boolean {
  return FIGURE_TEXT.test(text.trim());
}

/**
 * Parses a filing's viewer HTML into a fragment ready to insert, without the
 * images that cannot load here.
 *
 * Most filings keep EDGAR-relative image sources (`aapl-20260328_g1.jpg`) that
 * 404 against this origin. Hiding them once they fail is too late: by then the
 * highlight has scrolled to the cited figure, and the content above it shrinks
 * by the image's height, carrying the figure out of the pane. So they are
 * removed before the HTML goes live. A `<template>` parses inertly -- its
 * images are never requested -- which is why the HTML is parsed there and not
 * by assigning `innerHTML` on the live container.
 *
 * Figure cells are tagged with FIGURE_CELL_CLASS here too, so that on a phone
 * a squeezed column cannot break a number or let it spill out of its cell (the
 * cited-figure box then sliced through a digit) while text cells still wrap.
 * Only the live copy is tagged: stored HTML and sids are untouched, and cell
 * indexes (what citations' `cells` point at) do not change.
 */
export function parseViewerHtml(html: string, doc: Document = document): DocumentFragment {
  const template = doc.createElement("template");
  template.innerHTML = html;
  for (const img of template.content.querySelectorAll("img")) {
    if (!LOADABLE_SRC.test(img.getAttribute("src") ?? "")) img.remove();
  }
  for (const cell of template.content.querySelectorAll("td, th")) {
    if (isFigureText(cell.textContent ?? "")) cell.classList.add(FIGURE_CELL_CLASS);
  }
  return template.content;
}
