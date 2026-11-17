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
    const table = el.closest("table");
    if (table !== null) tables.add(table);
  }
  if (tables.size > 0) {
    for (const table of tables) out.append(table.cloneNode(true));
    return out;
  }

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
  out.append(paragraph);
  return out;
}
