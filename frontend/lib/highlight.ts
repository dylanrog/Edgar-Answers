import type { CitedCell } from "./types";

/** Plain global classes, defined in app/globals.css. They cannot be Tailwind
 *  utilities: these elements are injected as raw HTML (innerHTML), so Tailwind
 *  never sees them at build time. */
export const HIGHLIGHT_CLASS = "cited-sentence";
export const FIGURE_CLASS = "cited-figure";
/** A short attention pulse on a freshly applied highlight; the CSS does
 *  nothing under prefers-reduced-motion. */
export const PULSE_CLASS = "cited-pulse";

/** What one filing pane should highlight. */
export type Highlight = { sids: number[]; cells: CitedCell[] };

export const NO_HIGHLIGHT: Highlight = { sids: [], cells: [] };

/**
 * Highlight the sentences a citation resolves to, inside already-mounted HTML.
 * For a table row whose cited figures resolve, only those cells are marked
 * (spec 2026-09-29 §5.7); every other cited sentence or row is highlighted whole.
 *
 * Operates on the live container rather than rewriting the HTML string,
 * because a real 10-K's viewer_html is ~818 KB -- re-parsing that on every
 * citation click would be visibly slow, and regex-over-HTML is fragile.
 * Injection happens once per filing; this runs on every highlight change.
 *
 * Marks only; it does not scroll. Returns the element to bring into view (the
 * first cited figure, else the first cited sentence), or null when nothing
 * resolved -- the caller centres it with `centreInPane`.
 */
export function applyHighlight(
  container: HTMLElement,
  sids: number[],
  cells: CitedCell[] = [],
): Element | null {
  container
    .querySelectorAll(`.${HIGHLIGHT_CLASS}, .${FIGURE_CLASS}, .${PULSE_CLASS}`)
    .forEach((el) => el.classList.remove(HIGHLIGHT_CLASS, FIGURE_CLASS, PULSE_CLASS));

  // Collected into arrays rather than tracked in a `let` that a callback
  // assigns: TypeScript narrows such a variable to `null` at the use site and
  // reports `scrollIntoView` on type `never`.
  const figures: Element[] = [];
  const rowsWithFigures = new Set<number>();
  for (const { sid, cell } of cells) {
    const row = container.querySelector(`tr[data-sid="${sid}"]`);
    const target = row instanceof HTMLTableRowElement ? row.cells[cell] : undefined;
    if (target !== undefined) {
      target.classList.add(FIGURE_CLASS);
      figures.push(target);
      rowsWithFigures.add(sid);
    }
  }
  // A table row whose cited figure resolved is marked by that cell alone; the
  // whole-row highlight is only the fallback for a row with no resolvable
  // figure (a label-only quote, or a row whose cell spans are NULL).
  const cited: Element[] = [];
  for (const sid of sids) {
    if (rowsWithFigures.has(sid)) continue;
    container.querySelectorAll(`[data-sid="${sid}"]`).forEach((el) => {
      el.classList.add(HIGHLIGHT_CLASS);
      cited.push(el);
    });
  }
  for (const el of figures.length > 0 ? figures : cited) pulse(el);
  return figures[0] ?? cited[0] ?? null;
}

/**
 * Centres `target` in its scrolling `pane`, on both axes, by setting the
 * pane's own scroll offsets. Not `scrollIntoView`: that scrolls every
 * scrollable ancestor, the window included, so on a phone it dragged the page
 * away from the coach card to put the figure mid-screen. Offsets are clamped
 * by the browser, so a target near an edge simply lands as close as it can.
 */
export function centreInPane(
  pane: HTMLElement,
  target: Element,
  behavior: ScrollBehavior = "auto",
): void {
  const box = pane.getBoundingClientRect();
  const rect = target.getBoundingClientRect();
  pane.scrollTo({
    top: pane.scrollTop + rect.top - box.top - (pane.clientHeight - rect.height) / 2,
    left: pane.scrollLeft + rect.left - box.left - (pane.clientWidth - rect.width) / 2,
    behavior,
  });
}

const USER_INPUT = ["wheel", "touchstart", "pointerdown", "keydown"] as const;

function pulse(el: Element): void {
  el.classList.add(PULSE_CLASS);
  // Removed once played, so that clicking the same citation again replays it.
  // A row's animation runs on its cells; animationend bubbles up to the row.
  el.addEventListener("animationend", () => el.classList.remove(PULSE_CLASS), {
    once: true,
  });
}

/**
 * Keeps `target` centred in `pane` while the layout settles: for `ms` after a
 * highlight, any size change of a `watched` element (the injected HTML, the
 * pane itself) re-centres it, instantly. Something that lands just after the
 * highlight scrolled -- a late font, a coach card mounting above the viewer --
 * would otherwise leave the figure off-centre or out of the pane.
 *
 * The visitor's first wheel, touch, pointer press or key press ends it: from
 * then on they are steering, and a layout change must not yank them back.
 * Returns a stop function.
 */
export function keepCentred(
  pane: HTMLElement,
  target: Element,
  watched: Element[],
  ms = 2000,
): () => void {
  if (typeof ResizeObserver === "undefined") return () => {};
  // ResizeObserver reports every element once on observe(); only a change
  // from that first size is a layout shift.
  const sizes = new Map<Element, string>();
  const observer = new ResizeObserver((entries) => {
    let shifted = false;
    for (const entry of entries) {
      const size = `${entry.contentRect.width}x${entry.contentRect.height}`;
      const before = sizes.get(entry.target);
      sizes.set(entry.target, size);
      if (before !== undefined && before !== size) shifted = true;
    }
    if (shifted) centreInPane(pane, target);
  });
  for (const el of watched) observer.observe(el);
  const stop = () => {
    clearTimeout(timer);
    observer.disconnect();
    for (const type of USER_INPUT) window.removeEventListener(type, stop, true);
  };
  const timer = setTimeout(stop, ms);
  for (const type of USER_INPUT) {
    window.addEventListener(type, stop, { capture: true, passive: true, once: true });
  }
  return stop;
}
