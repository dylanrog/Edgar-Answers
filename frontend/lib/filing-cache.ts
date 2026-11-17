import { fetchFiling } from "./api";
import type { Filing } from "./types";
import { parseViewerHtml } from "./viewer-html";

export type ParsedFiling = { filing: Filing; content: DocumentFragment };

// One fetch and one parse per accession for the page's lifetime: a turn's
// evidence cards often cite the same filing twice, and a 10-K's viewer HTML is
// ~800 KB. A failed load is dropped so the next card can retry.
const cache = new Map<string, Promise<ParsedFiling>>();

export function loadParsedFiling(accession: string): Promise<ParsedFiling> {
  let entry = cache.get(accession);
  if (entry === undefined) {
    entry = fetchFiling(accession).then((filing) => ({
      filing,
      content: parseViewerHtml(filing.viewer_html),
    }));
    entry.catch(() => cache.delete(accession));
    cache.set(accession, entry);
  }
  return entry;
}
