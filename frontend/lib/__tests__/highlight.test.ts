/**
 * @vitest-environment jsdom
 */
import { beforeAll, beforeEach, expect, test, vi } from "vitest";

import { FIGURE_CLASS, HIGHLIGHT_CLASS, applyHighlight } from "../highlight";

beforeAll(() => {
  // jsdom implements no layout, so scrollIntoView does not exist on Element.
  // Without this stub every test here throws TypeError.
  Element.prototype.scrollIntoView = vi.fn();
});

let container: HTMLElement;

beforeEach(() => {
  vi.clearAllMocks();
  container = document.createElement("div");
  container.innerHTML = `
    <p><span data-sid="10">First.</span></p>
    <p><span data-sid="11">Second.</span></p>
    <p><span data-sid="12">Third.</span></p>`;
});

test("adds the class to every cited sid", () => {
  applyHighlight(container, [10, 12]);
  expect(container.querySelector('[data-sid="10"]')?.className).toBe(HIGHLIGHT_CLASS);
  expect(container.querySelector('[data-sid="12"]')?.className).toBe(HIGHLIGHT_CLASS);
  expect(container.querySelector('[data-sid="11"]')?.className).toBe("");
});

test("clears the previous highlight before applying the next", () => {
  applyHighlight(container, [10]);
  applyHighlight(container, [11]);
  expect(container.querySelector('[data-sid="10"]')?.className).toBe("");
  expect(container.querySelector('[data-sid="11"]')?.className).toBe(HIGHLIGHT_CLASS);
});

test("scrolls the first cited sentence into view", () => {
  applyHighlight(container, [11, 12]);
  const first = container.querySelector('[data-sid="11"]');
  expect(first?.scrollIntoView).toHaveBeenCalledTimes(1);
});

test("an unknown sid highlights nothing and does not throw", () => {
  expect(() => applyHighlight(container, [999])).not.toThrow();
  expect(container.querySelectorAll(`.${HIGHLIGHT_CLASS}`)).toHaveLength(0);
});

test("an empty sid list clears everything", () => {
  applyHighlight(container, [10]);
  applyHighlight(container, []);
  expect(container.querySelectorAll(`.${HIGHLIGHT_CLASS}`)).toHaveLength(0);
});

function tableContainer(): HTMLElement {
  const table = document.createElement("div");
  table.innerHTML = `
    <table><tbody>
      <tr data-sid="20"><td>Data Center</td><td>$</td><td>115,186</td><td>$</td><td>47,525</td></tr>
      <tr data-sid="21"><td>Compute</td><td>102,196</td><td>38,950</td></tr>
    </tbody></table>`;
  return table;
}

test("a cited cell gets the figure class inside the highlighted row", () => {
  const table = tableContainer();
  applyHighlight(table, [20], [{ sid: 20, cell: 2 }]);
  const cells = table.querySelectorAll('tr[data-sid="20"] td');
  expect(cells[2].classList.contains(FIGURE_CLASS)).toBe(true);
  expect(cells[4].classList.contains(FIGURE_CLASS)).toBe(false);
  expect(table.querySelector('tr[data-sid="20"]')?.classList.contains(HIGHLIGHT_CLASS)).toBe(true);
});

test("the cited figure is what scrolls into view", () => {
  const table = tableContainer();
  applyHighlight(table, [20], [{ sid: 20, cell: 2 }]);
  const figure = table.querySelectorAll('tr[data-sid="20"] td')[2];
  expect(figure.scrollIntoView).toHaveBeenCalledTimes(1);
});

test("the next highlight clears the previous figure", () => {
  const table = tableContainer();
  applyHighlight(table, [20], [{ sid: 20, cell: 2 }]);
  applyHighlight(table, [21], []);
  expect(table.querySelectorAll(`.${FIGURE_CLASS}`)).toHaveLength(0);
});

test("a cell index past the row's end is ignored", () => {
  const table = tableContainer();
  expect(() => applyHighlight(table, [21], [{ sid: 21, cell: 9 }])).not.toThrow();
  expect(table.querySelectorAll(`.${FIGURE_CLASS}`)).toHaveLength(0);
});
