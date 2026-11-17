import { fetchFiling } from "./api";
import type { Filing } from "./types";
import { parseViewerHtml } from "./viewer-html";

export type ParsedFiling = { content: DocumentFragment };

// One fetch and one parse per accession: a turn's evidence cards often cite
// the same filing twice, and a 10-K's viewer HTML is ~800 KB. Only the parsed
// tree is kept (not the HTML string), and only for the most recent filings.
// A failed load is dropped so the next card can retry.
const MAX_FILINGS = 8;
const cache = new Map<string, Promise<ParsedFiling>>();

export function loadParsedFiling(accession: string): Promise<ParsedFiling> {
  let entry = cache.get(accession);
  if (entry === undefined) {
    entry = fetchFiling(accession).then((filing: Filing) => ({
      content: parseViewerHtml(filing.viewer_html),
    }));
    entry.catch(() => cache.delete(accession));
  } else {
    cache.delete(accession);
  }
  // Map order is insertion order: re-inserting marks this one most recent.
  cache.set(accession, entry);
  while (cache.size > MAX_FILINGS) cache.delete(cache.keys().next().value as string);
  return entry;
}
