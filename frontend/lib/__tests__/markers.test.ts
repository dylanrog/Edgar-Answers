import { describe, expect, test } from "vitest";

import { splitOnMarkers, splitParagraphs } from "../markers";

test("text with no marker is one segment", () => {
  expect(splitOnMarkers("Just prose.")).toEqual(["Just prose."]);
});

test("splits around a single marker", () => {
  expect(splitOnMarkers("Sales rose [1].")).toEqual([
    "Sales rose ",
    { marker: 1 },
    ".",
  ]);
});

test("handles a multi-digit marker", () => {
  expect(splitOnMarkers("see [10]")).toEqual(["see ", { marker: 10 }]);
});

test("handles adjacent markers", () => {
  expect(splitOnMarkers("both [1][2] agree")).toEqual([
    "both ",
    { marker: 1 },
    { marker: 2 },
    " agree",
  ]);
});

test("leaves non-numeric brackets alone", () => {
  expect(splitOnMarkers("see Item [1A] here")).toEqual(["see Item [1A] here"]);
});

test("handles a marker at the very start", () => {
  expect(splitOnMarkers("[3] leads")).toEqual([{ marker: 3 }, " leads"]);
});

test("an empty string yields no segments", () => {
  expect(splitOnMarkers("")).toEqual([]);
});

describe("splitParagraphs", () => {
  test("splits at blank lines and keeps single line breaks", () => {
    expect(splitParagraphs("One [1].\n\nTwo\n- a [2]\n\n\n  Three.")).toEqual([
      "One [1].",
      "Two\n- a [2]",
      "Three.",
    ]);
  });

  test("a stream cut mid-break yields no empty paragraph", () => {
    expect(splitParagraphs("One [1].\n\n")).toEqual(["One [1]."]);
    expect(splitParagraphs("")).toEqual([]);
  });

  test("every marker survives the paragraph split", () => {
    const prose = "A [1] b.\n\nC [2] d [3].\n\nE [10].";
    const markers = (segments: ReturnType<typeof splitOnMarkers>) =>
      segments.filter((s) => typeof s !== "string");
    expect(splitParagraphs(prose).flatMap((p) => markers(splitOnMarkers(p)))).toEqual(
      markers(splitOnMarkers(prose)),
    );
  });
});
