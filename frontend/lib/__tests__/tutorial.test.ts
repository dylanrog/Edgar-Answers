import { expect, test, vi } from "vitest";

import { TUTORIAL, askFresh } from "../tutorial";

test("TUTORIAL carries a question and a follow-up", () => {
  expect(TUTORIAL.question.length).toBeGreaterThan(0);
  expect(TUTORIAL.followUp.length).toBeGreaterThan(0);
});

test("askFresh starts a new conversation before asking, with no filters", () => {
  const calls: string[] = [];
  const ask = vi.fn(() => void calls.push("ask"));
  askFresh("Q?", { newConversation: () => void calls.push("new"), ask });
  expect(calls).toEqual(["new", "ask"]);
  expect(ask).toHaveBeenCalledWith("Q?", {});
});
