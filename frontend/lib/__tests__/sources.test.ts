import { expect, test } from "vitest";

import { groupSources } from "../sources";
import type { Citation } from "../types";

function citation(marker: number, over: Partial<Citation> = {}): Citation {
  return {
    marker,
    verified: true,
    accession: "AAPL-1",
    ticker: "AAPL",
    form_type: "10-K",
    filing_date: "2024-11-01",
    sids: [marker],
    quote: `quote ${marker}`,
    cells: [],
    ...over,
  };
}

function map(...citations: Citation[]): Map<number, Citation> {
  return new Map(citations.map((c) => [c.marker, c]));
}

test("citations from one filing collapse into a single group", () => {
  const groups = groupSources(map(citation(1), citation(3)));
  expect(groups).toHaveLength(1);
  expect(groups[0].citations.map((c) => c.marker)).toEqual([1, 3]);
  expect(groups[0].verifiedCount).toBe(2);
});

test("groups are ordered by the first marker that appears in each", () => {
  // Insertion order is deliberately NOT marker order: AAPL's marker 2 is
  // inserted before MSFT's marker 1. An implementation that iterated the
  // Map directly instead of sorting would return ["AAPL-1", "MSFT-1"].
  const groups = groupSources(
    map(
      citation(2, { accession: "AAPL-1" }),
      citation(1, { accession: "MSFT-1", ticker: "MSFT" }),
      citation(3, { accession: "MSFT-1", ticker: "MSFT" }),
    ),
  );
  expect(groups.map((g) => g.accession)).toEqual(["MSFT-1", "AAPL-1"]);
});

test("a filing whose citations all failed still renders but does not open", () => {
  const groups = groupSources(map(citation(1, { verified: false, sids: [] })));
  expect(groups).toHaveLength(1);
  expect(groups[0].verifiedCount).toBe(0);
  expect(groups[0].openable).toBe(false);
});

test("a citation with no accession is unattributable and does not open", () => {
  const groups = groupSources(
    map(citation(1, { verified: false, accession: "", ticker: "", sids: [] })),
  );
  expect(groups[0].accession).toBe("");
  expect(groups[0].openable).toBe(false);
});

test("a partially verified filing is openable", () => {
  const groups = groupSources(map(citation(1), citation(2, { verified: false, sids: [] })));
  expect(groups[0].verifiedCount).toBe(1);
  expect(groups[0].openable).toBe(true);
});

test("no citations yields no groups", () => {
  expect(groupSources(new Map())).toEqual([]);
});
