/**
 * @vitest-environment jsdom
 */
import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

import {
  FIGURE_CLASS,
  HIGHLIGHT_CLASS,
  PULSE_CLASS,
  applyHighlight,
  keepCentred,
} from "../highlight";

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
  expect(container.querySelector('[data-sid="10"]')?.classList.contains(HIGHLIGHT_CLASS)).toBe(true);
  expect(container.querySelector('[data-sid="12"]')?.classList.contains(HIGHLIGHT_CLASS)).toBe(true);
  expect(container.querySelector('[data-sid="11"]')?.classList.contains(HIGHLIGHT_CLASS)).toBe(false);
});

test("clears the previous highlight before applying the next", () => {
  applyHighlight(container, [10]);
  applyHighlight(container, [11]);
  expect(container.querySelector('[data-sid="10"]')?.classList.contains(HIGHLIGHT_CLASS)).toBe(false);
  expect(container.querySelector('[data-sid="11"]')?.classList.contains(HIGHLIGHT_CLASS)).toBe(true);
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

test("a cited cell gets the figure class and its row is not highlighted", () => {
  const table = tableContainer();
  applyHighlight(table, [20], [{ sid: 20, cell: 2 }]);
  const cells = table.querySelectorAll('tr[data-sid="20"] td');
  expect(cells[2].classList.contains(FIGURE_CLASS)).toBe(true);
  expect(cells[4].classList.contains(FIGURE_CLASS)).toBe(false);
  expect(table.querySelector('tr[data-sid="20"]')?.classList.contains(HIGHLIGHT_CLASS)).toBe(false);
});

test("a cited row without a resolvable figure still highlights the whole row", () => {
  const table = tableContainer();
  applyHighlight(table, [20, 21], [{ sid: 20, cell: 2 }]);
  expect(table.querySelector('tr[data-sid="20"]')?.classList.contains(HIGHLIGHT_CLASS)).toBe(false);
  expect(table.querySelector('tr[data-sid="21"]')?.classList.contains(HIGHLIGHT_CLASS)).toBe(true);
});

test("a figure index that does not exist falls back to the row highlight", () => {
  const table = tableContainer();
  applyHighlight(table, [21], [{ sid: 21, cell: 9 }]);
  expect(table.querySelector('tr[data-sid="21"]')?.classList.contains(HIGHLIGHT_CLASS)).toBe(true);
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

test("returns the element it scrolled to, or null when nothing resolved", () => {
  const table = tableContainer();
  const figure = table.querySelectorAll('tr[data-sid="20"] td')[2];
  expect(applyHighlight(table, [20], [{ sid: 20, cell: 2 }])).toBe(figure);
  expect(applyHighlight(container, [999])).toBeNull();
});

test("a fresh highlight pulses the cited figure only", () => {
  const table = tableContainer();
  applyHighlight(table, [20], [{ sid: 20, cell: 2 }]);
  expect([...table.querySelectorAll(`.${PULSE_CLASS}`)]).toEqual([
    table.querySelectorAll('tr[data-sid="20"] td')[2],
  ]);
});

test("a fresh highlight pulses every cited sentence, and the pulse clears once played", () => {
  applyHighlight(container, [10, 12]);
  const pulsing = container.querySelectorAll(`.${PULSE_CLASS}`);
  expect(pulsing).toHaveLength(2);
  pulsing[0].dispatchEvent(new Event("animationend"));
  expect(container.querySelectorAll(`.${PULSE_CLASS}`)).toHaveLength(1);
});

test("the next highlight clears the previous pulse", () => {
  applyHighlight(container, [10]);
  applyHighlight(container, [11]);
  expect(container.querySelector('[data-sid="10"]')?.classList.contains(PULSE_CLASS)).toBe(false);
  expect(container.querySelector('[data-sid="11"]')?.classList.contains(PULSE_CLASS)).toBe(true);
});

describe("keepCentred", () => {
  type Callback = (entries: { target: Element; contentRect: { width: number; height: number } }[]) => void;
  let observers: { callback: Callback; observed: Element[]; disconnected: boolean }[];

  beforeEach(() => {
    vi.useFakeTimers();
    observers = [];
    vi.stubGlobal(
      "ResizeObserver",
      class {
        record: { callback: Callback; observed: Element[]; disconnected: boolean };
        constructor(callback: Callback) {
          this.record = { callback, observed: [], disconnected: false };
          observers.push(this.record);
        }
        observe(el: Element) {
          this.record.observed.push(el);
        }
        disconnect() {
          this.record.disconnected = true;
        }
      },
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const size = (target: Element, height: number) => ({
    target,
    contentRect: { width: 300, height },
  });

  test("re-centres the target when a watched element changes size", () => {
    const target = container.querySelector('[data-sid="11"]')!;
    keepCentred(target, [container]);
    const { callback, observed } = observers[0];
    expect(observed).toEqual([container]);
    callback([size(container, 500)]); // the initial report is not a shift
    expect(target.scrollIntoView).not.toHaveBeenCalled();
    callback([size(container, 1400)]);
    expect(target.scrollIntoView).toHaveBeenCalledWith({ block: "center" });
  });

  test("stops watching after the window, or when stopped", () => {
    const target = container.querySelector('[data-sid="11"]')!;
    keepCentred(target, [container], 2000);
    vi.advanceTimersByTime(1999);
    expect(observers[0].disconnected).toBe(false);
    vi.advanceTimersByTime(1);
    expect(observers[0].disconnected).toBe(true);

    const stop = keepCentred(target, [container], 2000);
    stop();
    expect(observers[1].disconnected).toBe(true);
  });
});
