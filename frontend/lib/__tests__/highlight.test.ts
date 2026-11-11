/**
 * @vitest-environment jsdom
 */
import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

import {
  FIGURE_CLASS,
  HIGHLIGHT_CLASS,
  PULSE_CLASS,
  applyHighlight,
  centreInPane,
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

test("targets the first cited sentence, and never scrolls the window itself", () => {
  const target = applyHighlight(container, [11, 12]);
  expect(target).toBe(container.querySelector('[data-sid="11"]'));
  // scrollIntoView would scroll every ancestor, the window included.
  expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
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

test("a two-row citation with cells only on row B: row A whole, row B only its cell", () => {
  // The server drops cell emphasis on a cited row whose figures the answer
  // never states (design §6.3 step 4); that row must still read as cited.
  const table = tableContainer();
  applyHighlight(table, [20, 21], [{ sid: 21, cell: 1 }]);
  const rowA = table.querySelector('tr[data-sid="20"]');
  const rowB = table.querySelector('tr[data-sid="21"]');
  expect(rowA?.classList.contains(HIGHLIGHT_CLASS)).toBe(true);
  expect(rowA?.querySelectorAll(`.${FIGURE_CLASS}`)).toHaveLength(0);
  expect(rowB?.classList.contains(HIGHLIGHT_CLASS)).toBe(false);
  const marked = Array.from(rowB?.querySelectorAll("td") ?? []).map((td) =>
    td.classList.contains(FIGURE_CLASS),
  );
  expect(marked).toEqual([false, true, false]);
});

test("a figure index that does not exist falls back to the row highlight", () => {
  const table = tableContainer();
  applyHighlight(table, [21], [{ sid: 21, cell: 9 }]);
  expect(table.querySelector('tr[data-sid="21"]')?.classList.contains(HIGHLIGHT_CLASS)).toBe(true);
});

test("the cited figure is the target", () => {
  const table = tableContainer();
  const figure = table.querySelectorAll('tr[data-sid="20"] td')[2];
  expect(applyHighlight(table, [20], [{ sid: 20, cell: 2 }])).toBe(figure);
});

function rect(left: number, top: number, width: number, height: number): DOMRect {
  return { left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) } as DOMRect;
}

/** A pane 400 x 300 at (100, 50) on screen, already scrolled to (40, 1000). */
function fakePane(): HTMLElement {
  const pane = document.createElement("div");
  pane.getBoundingClientRect = () => rect(100, 50, 400, 300);
  Object.defineProperty(pane, "clientWidth", { value: 400 });
  Object.defineProperty(pane, "clientHeight", { value: 300 });
  Object.defineProperty(pane, "scrollTop", { value: 1000 });
  Object.defineProperty(pane, "scrollLeft", { value: 40 });
  pane.scrollTo = vi.fn() as unknown as HTMLElement["scrollTo"];
  return pane;
}

test("centreInPane centres the target on both axes by scrolling the pane only", () => {
  const pane = fakePane();
  const target = document.createElement("td");
  // 60 x 20 at (600, 900): right of and below the visible area.
  target.getBoundingClientRect = () => rect(600, 900, 60, 20);
  centreInPane(pane, target, "smooth");
  expect(pane.scrollTo).toHaveBeenCalledWith({
    top: 1000 + (900 - 50) - (300 - 20) / 2,
    left: 40 + (600 - 100) - (400 - 60) / 2,
    behavior: "smooth",
  });
  expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
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

test("a target wider than the pane is aligned by its left edge, a little inset", () => {
  const pane = fakePane(); // 400 x 300 at (100, 50), scrolled to (40, 1000)
  const row = document.createElement("tr");
  row.getBoundingClientRect = () => rect(150, 900, 900, 20);
  centreInPane(pane, row);
  expect(pane.scrollTo).toHaveBeenCalledWith({
    top: 1000 + (900 - 50) - (300 - 20) / 2,
    left: 40 + (150 - 100) - 20,
    behavior: "auto",
  });
});

test("a target taller than the pane is aligned by its top edge, a little inset", () => {
  const pane = fakePane();
  const sentence = document.createElement("span");
  sentence.getBoundingClientRect = () => rect(200, 900, 100, 500);
  centreInPane(pane, sentence);
  expect(pane.scrollTo).toHaveBeenCalledWith({
    top: 1000 + (900 - 50) - 20,
    left: 40 + (200 - 100) - (400 - 100) / 2,
    behavior: "auto",
  });
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

  test("re-centres the target in the pane, instantly, when a watched element changes size", () => {
    const pane = fakePane();
    const target = container.querySelector('[data-sid="11"]')!;
    keepCentred(pane, target, [container, pane]);
    const { callback, observed } = observers[0];
    expect(observed).toEqual([container, pane]);
    callback([size(container, 500), size(pane, 300)]); // initial reports are not shifts
    expect(pane.scrollTo).not.toHaveBeenCalled();
    callback([size(pane, 120)]); // a card mounted above the viewer
    expect(pane.scrollTo).toHaveBeenCalledTimes(1);
    expect(pane.scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: "auto" }));
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  test("stops watching after the window, or when stopped", () => {
    const target = container.querySelector('[data-sid="11"]')!;
    keepCentred(fakePane(), target, [container], 2000);
    vi.advanceTimersByTime(1999);
    expect(observers[0].disconnected).toBe(false);
    vi.advanceTimersByTime(1);
    expect(observers[0].disconnected).toBe(true);

    const stop = keepCentred(fakePane(), target, [container], 2000);
    stop();
    expect(observers[1].disconnected).toBe(true);
  });

  test.each(["wheel", "touchstart", "pointerdown", "keydown"])(
    "the visitor's first %s ends it",
    (type) => {
      const pane = fakePane();
      const target = container.querySelector('[data-sid="11"]')!;
      keepCentred(pane, target, [container], 2000);
      window.dispatchEvent(new Event(type));
      expect(observers[0].disconnected).toBe(true);
    },
  );
});
