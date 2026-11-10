/** An image source that can load from this origin: absolute http(s) or inline data. */
const LOADABLE_SRC = /^\s*(https?:|data:)/i;

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
 */
export function parseViewerHtml(html: string, doc: Document = document): DocumentFragment {
  const template = doc.createElement("template");
  template.innerHTML = html;
  for (const img of template.content.querySelectorAll("img")) {
    if (!LOADABLE_SRC.test(img.getAttribute("src") ?? "")) img.remove();
  }
  return template.content;
}
