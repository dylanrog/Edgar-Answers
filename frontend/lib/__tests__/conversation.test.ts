/**
 * @vitest-environment jsdom
 */
import { afterEach, expect, test } from "vitest";

import { getOrCreateConversationId, startNewConversation } from "../conversation";

afterEach(() => sessionStorage.clear());

test("the id is stable within a session", () => {
  expect(getOrCreateConversationId()).toBe(getOrCreateConversationId());
});

test("it survives a simulated reload (value already in sessionStorage)", () => {
  const first = getOrCreateConversationId();
  // A reload keeps sessionStorage but loses module state — mimic by reading raw.
  expect(sessionStorage.getItem("edgar-answers.conversation-id")).toBe(first);
});

test("startNewConversation forces a fresh id on the next call", () => {
  const first = getOrCreateConversationId();
  startNewConversation();
  expect(getOrCreateConversationId()).not.toBe(first);
});

test("the id looks like a UUID", () => {
  expect(getOrCreateConversationId()).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
  );
});
