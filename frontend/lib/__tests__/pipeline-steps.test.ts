import { expect, test } from "vitest";

import { initialAnswerState } from "../answer";
import type { AnswerState } from "../answer";
import { buildPanel, isSettled } from "../pipeline-steps";
import type { Citation } from "../types";

const filing = (n: number) => ({
  accession: `A${n}`,
  ticker: "AAPL",
  form_type: "10-K",
  filing_date: "2024-11-01",
});

function cite(marker: number, over: Partial<Citation> = {}): Citation {
  return {
    marker,
    verified: true,
    accession: "A1",
    ticker: "AAPL",
    form_type: "10-K",
    filing_date: "2024-11-01",
    sids: [4, 6],
    quote: "q",
    cells: [],
    ...over,
  };
}

function state(over: Partial<AnswerState> = {}): AnswerState {
  return { ...initialAnswerState, status: "streaming", ...over };
}

const full = (citations: Citation[], over: Partial<AnswerState> = {}) =>
  state({
    prose: "text",
    citations: new Map(citations.map((c) => [c.marker, c])),
    progress: {
      targets: { tickers: ["AAPL"], filings: [] },
      retrieved: { passages: 8, filings: [{ ...filing(1), passages: 8 }] },
      quotes: citations.length,
    },
    ...over,
  });

test("before any progress there is one running targets line", () => {
  const lines = buildPanel(state(), {}, 0);
  expect(lines).toHaveLength(1);
  expect(lines[0]).toMatchObject({ key: "targets", status: "running" });
});

test("search counts the distinct filings", () => {
  const s = full([]);
  s.progress.retrieved = {
    passages: 8,
    filings: [1, 2, 3].map((n) => ({ ...filing(n), passages: 2 })),
  };
  expect(buildPanel(s, {}, 0).find((l) => l.key === "search")?.text).toBe(
    "Searched the filings: found 8 passages in 3 filings",
  );
});

test("names map tickers and unmapped tickers fall back", () => {
  const s = state({
    progress: { targets: { tickers: ["AAPL", "ZZZ"], filings: [] }, retrieved: null, quotes: null },
  });
  expect(buildPanel(s, { AAPL: "Apple" }, 0)[0].text).toBe(
    "Understood the question: Apple, ZZZ",
  );
});

test("no tickers means every company", () => {
  const s = state({
    progress: { targets: { tickers: [], filings: [] }, retrieved: null, quotes: null },
  });
  expect(buildPanel(s, {}, 0)[0].text).toBe("Searching every company");
});

test("narrowed filings are appended", () => {
  const s = state({
    progress: { targets: { tickers: ["AAPL"], filings: [filing(1)] }, retrieved: null, quotes: null },
  });
  expect(buildPanel(s, { AAPL: "Apple" }, 0)[0].text).toBe(
    "Understood the question: Apple · AAPL 10-K 2024",
  );
});

test("revealed limits the quote lines", () => {
  const lines = buildPanel(full([cite(1), cite(2)]), {}, 1);
  expect(lines.filter((l) => l.key.startsWith("quote-"))).toHaveLength(1);
});

test("unverified citation is a failed line", () => {
  const lines = buildPanel(full([cite(1, { verified: false })]), {}, 1);
  const line = lines.find((l) => l.key === "quote-1");
  expect(line?.status).toBe("failed");
  expect(line?.text).toContain("not found in the source");
});

test("verified citation text and table figure detail", () => {
  const lines = buildPanel(full([cite(1, { cells: [{ sid: 4, cell: 2 }] })]), {}, 1);
  const line = lines.find((l) => l.key === "quote-1");
  expect(line?.text).toBe("Quote 1 → AAPL 10-K 2024 ✓ found word for word");
  expect(line?.detail).toContain("sentences 4–6");
  expect(line?.detail).toContain("table figure");
});

test("an error mid-stream fails the running line", () => {
  const s = state({
    status: "error",
    progress: { targets: { tickers: [], filings: [] }, retrieved: null, quotes: null },
  });
  const lines = buildPanel(s, {}, 0);
  expect(lines.at(-1)?.status).toBe("failed");
});

test("isSettled needs done and every citation revealed", () => {
  const s = full([cite(1), cite(2)], { status: "done" });
  expect(isSettled(s, 1)).toBe(false);
  expect(isSettled(s, 2)).toBe(true);
  expect(isSettled(full([cite(1)]), 1)).toBe(false);
});

test("isSettled covers error, zero citations and done without quotes", () => {
  expect(isSettled(state({ status: "error" }), 0)).toBe(true);
  expect(isSettled(state({ status: "done" }), 0)).toBe(true);
  expect(isSettled(state({ status: "streaming" }), 0)).toBe(false);
  const noQuotes = state({ status: "done", citations: new Map([[1, cite(1)]]) });
  expect(isSettled(noQuotes, 0)).toBe(false);
  expect(isSettled(noQuotes, 1)).toBe(true);
});

test("done without progress events leaves no spinner and no detail", () => {
  const lines = buildPanel(state({ status: "done" }), {}, 0);
  expect(lines.some((l) => l.status === "running")).toBe(false);
  expect(lines[0].detail).toBeUndefined();
});

test("done without a quotes event still lists revealed quotes", () => {
  const s = state({ status: "done", citations: new Map([[1, cite(1)]]) });
  expect(buildPanel(s, {}, 1).some((l) => l.key === "quote-1")).toBe(true);
});

test("a late error after the reveal marks a line failed", () => {
  const s = full([cite(1)], { status: "error" });
  const lines = buildPanel(s, {}, 1);
  expect(lines.some((l) => l.status === "failed")).toBe(true);
  expect(lines.find((l) => l.key === "check")?.status).toBe("failed");
});

test("a single-sentence citation says sentence, not a range", () => {
  const lines = buildPanel(full([cite(1, { sids: [31] })]), {}, 1);
  expect(lines.find((l) => l.key === "quote-1")?.detail).toBe("sentence 31");
});

test("an error before any progress event is a generic failure, not a detection failure", () => {
  const lines = buildPanel(state({ status: "error" }), {}, 0);
  expect(lines).toEqual([
    { key: "error", status: "failed", text: "Stopped: the request failed" },
  ]);
});
