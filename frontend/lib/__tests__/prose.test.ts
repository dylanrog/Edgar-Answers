import { describe, expect, it } from "vitest";
import { parseProse } from "../prose";

describe("parseProse", () => {
  it("turns a leading # line into a heading without the #", () => {
    expect(parseProse("## Title\n\nBody")).toEqual([
      { kind: "heading", level: 2, inlines: [{ text: "Title", bold: false }] },
      { kind: "paragraph", inlines: [{ text: "Body", bold: false }] },
    ]);
  });

  it("splits a heading directly followed by text on the next line", () => {
    const blocks = parseProse("# Head\nBody line");
    expect(blocks.map((b) => b.kind)).toEqual(["heading", "paragraph"]);
  });

  it("renders **bold** as a bold inline", () => {
    expect(parseProse("a **b** c")[0]).toEqual({
      kind: "paragraph",
      inlines: [
        { text: "a ", bold: false },
        { text: "b", bold: true },
        { text: " c", bold: false },
      ],
    });
  });

  it("keeps markers working inside bold and headings", () => {
    expect(parseProse("**x [1]**")[0]).toEqual({
      kind: "paragraph",
      inlines: [
        { text: "x ", bold: true },
        { marker: 1, bold: true },
      ],
    });
    expect(parseProse("# H [2]")[0]).toMatchObject({
      kind: "heading",
      inlines: [{ text: "H ", bold: false }, { marker: 2, bold: false }],
    });
  });

  it("leaves an unclosed ** literal while streaming", () => {
    expect(parseProse("a **b")[0]).toEqual({
      kind: "paragraph",
      inlines: [{ text: "a **b", bold: false }],
    });
  });

  it("leaves # not at line start, and #hashtag, literal", () => {
    expect(parseProse("see # not")[0].kind).toBe("paragraph");
    expect(parseProse("#hashtag")[0]).toEqual({
      kind: "paragraph",
      inlines: [{ text: "#hashtag", bold: false }],
    });
    expect(parseProse("####### seven")[0].kind).toBe("paragraph");
  });
});
