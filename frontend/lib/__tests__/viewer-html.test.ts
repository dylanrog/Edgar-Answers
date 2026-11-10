/**
 * @vitest-environment jsdom
 */
import { expect, test } from "vitest";

import { parseViewerHtml } from "../viewer-html";

function srcs(fragment: DocumentFragment): (string | null)[] {
  return [...fragment.querySelectorAll("img")].map((img) => img.getAttribute("src"));
}

test("drops every image whose source cannot load from this origin", () => {
  const fragment = parseViewerHtml(`
    <p><span data-sid="1">Before.</span></p>
    <img src="aapl-20260328_g1.jpg">
    <img src="/images/logo.gif">
    <img src="../g2.png">
    <img>
    <p><span data-sid="2">After.</span></p>`);
  expect(srcs(fragment)).toEqual([]);
});

test("keeps absolute http(s) and data images", () => {
  const fragment = parseViewerHtml(`
    <img src="https://www.sec.gov/a.jpg">
    <img src="HTTP://example.com/b.png">
    <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">`);
  expect(srcs(fragment)).toEqual([
    "https://www.sec.gov/a.jpg",
    "HTTP://example.com/b.png",
    "data:image/gif;base64,R0lGODlhAQABAAAAACw=",
  ]);
});

test("leaves the sentence markup untouched", () => {
  const html = `<p><span data-sid="7">Revenue grew.</span></p><table><tbody><tr data-sid="8"><td>Total</td><td>1</td></tr></tbody></table>`;
  const host = document.createElement("div");
  host.append(parseViewerHtml(`<img src="x.jpg">${html}`));
  expect(host.innerHTML).toBe(html);
});
