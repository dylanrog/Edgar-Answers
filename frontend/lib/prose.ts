import { splitOnMarkers, splitParagraphs, type Segment } from "./markers";

export type Inline = { text: string; bold: boolean } | { marker: number; bold: boolean };
export type ProseBlock =
  | { kind: "paragraph"; inlines: Inline[] }
  | { kind: "heading"; level: number; inlines: Inline[] };

/**
 * Parse a line of prose into inlines: `**bold**` spans and `[n]` markers.
 * Anything unmatched (an unclosed `**` mid-stream) stays literal text.
 */
export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  const push = (chunk: string, bold: boolean) => {
    for (const seg of splitOnMarkers(chunk) as Segment[]) {
      out.push(typeof seg === "string" ? { text: seg, bold } : { marker: seg.marker, bold });
    }
  };
  let cursor = 0;
  for (const match of text.matchAll(/\*\*([^*\n]+)\*\*/g)) {
    const index = match.index ?? 0;
    push(text.slice(cursor, index), false);
    push(match[1], true);
    cursor = index + match[0].length;
  }
  push(text.slice(cursor), false);
  return out;
}

/**
 * Minimal, safe Markdown subset for answer prose: `#`-`######` + space at the
 * start of a line is a heading, `**x**` is bold. Nothing else is interpreted.
 */
export function parseProse(text: string): ProseBlock[] {
  const blocks: ProseBlock[] = [];
  for (const paragraph of splitParagraphs(text)) {
    let pending: string[] = [];
    const flush = () => {
      if (pending.length) {
        blocks.push({ kind: "paragraph", inlines: parseInline(pending.join("\n")) });
        pending = [];
      }
    };
    for (const line of paragraph.split("\n")) {
      const heading = /^(#{1,6}) +(\S.*)$/.exec(line);
      if (heading) {
        flush();
        blocks.push({
          kind: "heading",
          level: heading[1].length,
          inlines: parseInline(heading[2]),
        });
      } else {
        pending.push(line);
      }
    }
    flush();
  }
  return blocks;
}
