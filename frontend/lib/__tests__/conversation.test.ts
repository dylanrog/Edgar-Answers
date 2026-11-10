/**
 * @vitest-environment jsdom
 */
import { afterEach, describe, expect, test, vi } from "vitest";

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

describe("when sessionStorage is unavailable", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    startNewConversation();
  });

  function disableStorage() {
    for (const method of ["getItem", "setItem", "removeItem"] as const) {
      vi.spyOn(Storage.prototype, method).mockImplementation(() => {
        throw new Error("storage disabled");
      });
    }
  }

  test("the id is still stable between questions, so follow-ups keep context", () => {
    disableStorage();
    const first = getOrCreateConversationId();
    expect(first).toMatch(/^[0-9a-f-]{36}$/i);
    expect(getOrCreateConversationId()).toBe(first);
  });

  test("startNewConversation still starts a fresh one", () => {
    disableStorage();
    const first = getOrCreateConversationId();
    startNewConversation();
    expect(getOrCreateConversationId()).not.toBe(first);
  });
});
