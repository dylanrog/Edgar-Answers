import type { CitedCell } from "./types";

/** Plain global classes, defined in app/globals.css. They cannot be Tailwind
 *  utilities: these elements come from dangerouslySetInnerHTML, so Tailwind
 *  never sees them at build time. */
export const HIGHLIGHT_CLASS = "cited-sentence";
export const FIGURE_CLASS = "cited-figure";

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
 */
export function applyHighlight(
  container: HTMLElement,
  sids: number[],
  cells: CitedCell[] = [],
): void {
  container
    .querySelectorAll(`.${HIGHLIGHT_CLASS}, .${FIGURE_CLASS}`)
    .forEach((el) => el.classList.remove(HIGHLIGHT_CLASS, FIGURE_CLASS));

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
  (figures[0] ?? cited[0])?.scrollIntoView({ behavior: "smooth", block: "center" });
}
