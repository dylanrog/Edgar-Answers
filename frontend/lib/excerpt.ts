/**
 * The slice of a filing an evidence card shows (spec 2026-11-17 §4): enough of
 * the real filing to read the cited text in context, without the whole filing.
 *
 * - A citation on a table row gets its whole table, so the column headers and
 *   neighbouring rows come along. Tables are where the figures live.
 * - A citation on prose gets the cited sentences plus one sentence either side.
 *
 * Elements are cloned with their data-sid attributes, so `applyHighlight`
 * marks the excerpt exactly as it marks the full filing. Returns null when
 * none of the sids exists in the filing.
 */
export function buildExcerpt(
  filing: ParentNode,
  sids: number[],
  doc: Document = document,
): DocumentFragment | null {
  if (sids.length === 0) return null;
  const cited = sids.flatMap((sid) => [...filing.querySelectorAll(`[data-sid="${sid}"]`)]);
  if (cited.length === 0) return null;

  const out = doc.createDocumentFragment();
  const tables = new Set<Element>();
  for (const el of cited) {
    const table = outermostTable(el);
    if (table !== null) tables.add(table);
  }
  for (const table of tables) out.append(table.cloneNode(true));
  // A citation can span a table row and a sentence: the prose part gets its
  // window too, after the table, rather than being dropped.
  const proseSids = sids.filter((sid) =>
    [...filing.querySelectorAll(`[data-sid="${sid}"]`)].some((el) => el.closest("table") === null),
  );
  if (proseSids.length > 0) out.append(proseWindow(filing, proseSids, doc));
  // Cloned ids would duplicate the drawer's, so an in-filing anchor could jump
  // to a card behind it.
  for (const el of out.querySelectorAll("[id]")) el.removeAttribute("id");
  return out;
}

/** A row in a nested table belongs with the outer table's header rows. */
function outermostTable(el: Element): Element | null {
  let table = el.closest("table");
  while (table?.parentElement?.closest("table")) table = table.parentElement.closest("table");
  return table;
}

function proseWindow(filing: ParentNode, sids: number[], doc: Document): HTMLParagraphElement {
  const first = Math.min(...sids) - 1;
  const last = Math.max(...sids) + 1;
  const paragraph = doc.createElement("p");
  const citedSids = new Set(sids);
  for (let sid = first; sid <= last; sid += 1) {
    for (const el of filing.querySelectorAll(`[data-sid="${sid}"]`)) {
      // A neighbouring table row is not prose context: leave it out.
      if (el.closest("table") !== null) continue;
      // Nor is a neighbour with no words, such as a list's "•" bullet.
      if (!citedSids.has(sid) && !/[A-Za-z0-9]/.test(el.textContent ?? "")) continue;
      if (paragraph.childNodes.length > 0) paragraph.append(" ");
      paragraph.append(el.cloneNode(true));
    }
  }
  return paragraph;
}
