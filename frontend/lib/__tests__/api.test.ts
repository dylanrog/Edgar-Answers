import { afterEach, expect, test, vi } from "vitest";

import { askStream } from "../api";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("a 429 surfaces the server's own explanation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify({ detail: "Limit reached for today." }), { status: 429 }),
    ),
  );
  await expect(askStream("q").next()).rejects.toThrow("Limit reached for today.");
});

test("a 429 without a JSON body still reads as a rate limit", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 429 })));
  await expect(askStream("q").next()).rejects.toThrow(/rate-limited/);
});

test("other failures still report their status", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("boom", { status: 500 })));
  await expect(askStream("q").next()).rejects.toThrow("Ask failed with status 500");
});
