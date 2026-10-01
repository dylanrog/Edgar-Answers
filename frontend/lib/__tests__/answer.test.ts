import { expect, test } from "vitest";

import { initialAnswerState, reduceAnswer } from "../answer";
import type { AnswerState } from "../answer";

function feed(events: Array<[string, unknown]>): AnswerState {
  return events.reduce(
    (state, [event, data]) => reduceAnswer(state, { event, data }),
    initialAnswerState,
  );
}

test("token events concatenate into prose and mark it streaming", () => {
  const state = feed([
    ["token", { text: "Net sales " }],
    ["token", { text: "rose [1]." }],
  ]);
  expect(state.prose).toBe("Net sales rose [1].");
  expect(state.status).toBe("streaming");
});

test("a citation event is stored under its marker", () => {
  const state = feed([
    ["citation", { marker: 1, verified: true, accession: "A", ticker: "AAPL", form_type: "10-K", filing_date: "2024-11-01", sids: [7], quote: "q" }],
  ]);
  expect(state.citations.get(1)?.sids).toEqual([7]);
});

test("done records counts and finishes", () => {
  const state = feed([
    [
      "done",
      {
        chunks_retrieved: 8,
        citations_total: 1,
        citations_verified: 1,
        unverified_answer: false,
      },
    ],
  ]);
  expect(state.status).toBe("done");
  expect(state.chunksRetrieved).toBe(8);
  expect(state.notice).toBeNull();
});

test("unverified_answer raises a notice", () => {
  const state = feed([
    [
      "done",
      {
        chunks_retrieved: 8,
        citations_total: 0,
        citations_verified: 0,
        unverified_answer: true,
      },
    ],
  ]);
  expect(state.notice).not.toBeNull();
});

test("an error keeps the prose already streamed", () => {
  const state = feed([
    ["token", { text: "Partial answer" }],
    ["error", { message: "upstream is down" }],
  ]);
  expect(state.status).toBe("error");
  expect(state.prose).toBe("Partial answer");
  expect(state.errorMessage).toContain("upstream is down");
});

test("an unknown event leaves state untouched", () => {
  const state = feed([["heartbeat", {}]]);
  expect(state).toEqual(initialAnswerState);
});

test("reduce does not mutate the state it was given", () => {
  const next = reduceAnswer(initialAnswerState, {
    event: "token",
    data: { text: "x" },
  });
  expect(initialAnswerState.prose).toBe("");
  expect(next).not.toBe(initialAnswerState);
});

test("a resolved event records the standalone question", () => {
  const state = feed([
    ["resolved", { standalone_question: "What was Apple revenue in fiscal 2023?" }],
    ["token", { text: "It was 383 billion." }],
  ]);
  expect(state.standaloneQuestion).toBe("What was Apple revenue in fiscal 2023?");
  expect(state.prose).toBe("It was 383 billion.");
});

test("standaloneQuestion starts null", () => {
  expect(initialAnswerState.standaloneQuestion).toBeNull();
});

test("a citation event without cells gets an empty cell list", () => {
  const state = feed([
    ["citation", { marker: 1, verified: true, accession: "A", ticker: "AAPL", form_type: "10-K", filing_date: "2024-11-01", sids: [7], quote: "q" }],
  ]);
  expect(state.citations.get(1)?.cells).toEqual([]);
});

test("a citation event keeps its cited cells", () => {
  const state = feed([
    ["citation", { marker: 1, verified: true, accession: "A", ticker: "AAPL", form_type: "10-K", filing_date: "2024-11-01", sids: [7], quote: "q", cells: [{ sid: 7, cell: 2 }] }],
  ]);
  expect(state.citations.get(1)?.cells).toEqual([{ sid: 7, cell: 2 }]);
});
