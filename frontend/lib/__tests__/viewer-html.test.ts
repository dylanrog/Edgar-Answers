/**
 * @vitest-environment jsdom
 */
import { expect, test } from "vitest";

import { FIGURE_CELL_CLASS, isFigureText, parseViewerHtml } from "../viewer-html";

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
  const html = `<p><span data-sid="7">Revenue grew.</span></p><table><tbody><tr data-sid="8"><td>Total</td><td>Net</td></tr></tbody></table>`;
  const host = document.createElement("div");
  host.append(parseViewerHtml(`<img src="x.jpg">${html}`));
  expect(host.innerHTML).toBe(html);
});

test.each([
  "115,186", "$ 47,525", "$47,525", "47,525 ", "(1,234)", "$ (1,234)", "1,234)",
  "12.5%", "142 %", "(12.5)%", "—12", "-3.4", "2024", "−12", "− 12", "(−1,234)",
])("%j is a figure", (text) => {
  expect(isFigureText(text)).toBe(true);
});

test.each([
  "$", "Data Center", "Total − net", "Jan 28, 2024", "Fiscal 2025", "—", "", "Item 7A", "10-K",
  "Revenue from the sale of products and services, net of returns, was 1,234",
])("%j is not a figure", (text) => {
  expect(isFigureText(text)).toBe(false);
});

test("tags figure cells only, keeping every cell in place (citation cell indexes)", () => {
  const host = document.createElement("div");
  host.append(
    parseViewerHtml(
      `<table><tbody><tr data-sid="9"><td>Data Center</td><td>$</td><td><span>47,525 </span></td><th>(12.5)%</th><td></td></tr></tbody></table>`,
    ),
  );
  const cells = [...host.querySelectorAll("tr")[0].cells];
  expect(cells.map((c) => c.classList.contains(FIGURE_CELL_CLASS))).toEqual([
    false, false, true, true, false,
  ]);
  expect(cells.map((c) => c.textContent)).toEqual(["Data Center", "$", "47,525 ", "(12.5)%", ""]);
});
