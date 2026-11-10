import { expect, test, vi } from "vitest";

vi.mock("../examples.json", () => ({
  default: [
    { id: "a", label: "A", question: "qa", kind: "prose", featured: false },
    { id: "b", label: "B", question: "qb", kind: "table", featured: true },
  ],
}));

import { askExampleFresh, featuredExample } from "../examples";

test("featuredExample returns the single featured entry", () => {
  expect(featuredExample()?.id).toBe("b");
});

test("askExampleFresh starts a new conversation before asking, with empty filters", () => {
  const calls: string[] = [];
  const ask = vi.fn((q: string) => void calls.push(`ask:${q}`));
  const example = { id: "a", label: "A", question: "qa", kind: "prose" as const, featured: false };
  askExampleFresh(
    example,
    { newConversation: () => void calls.push("new"), ask },
  );
  expect(calls).toEqual(["new", "ask:qa"]);
  expect(ask).toHaveBeenCalledWith("qa", {});
});
