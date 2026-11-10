import { afterEach, describe, expect, test } from "vitest";

import {
  askEvent,
  type TourEvent,
  type TourStep,
  firstVerifiedMarker,
  hasSeenTour,
  markTourSeen,
  reduceTour,
} from "../tour";
import type { Citation } from "../types";

const KEY = "edgar-answers.tour-seen";

afterEach(() => {
  delete (globalThis as { localStorage?: unknown }).localStorage;
});

function fakeStorage(): Storage {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, String(v)),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
    key: () => null,
    get length() {
      return data.size;
    },
  } as Storage;
}

function throwingStorage() {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get() {
      throw new Error("storage disabled");
    },
  });
}

describe("reduceTour transition table", () => {
  test.each<[TourStep, TourEvent, TourStep]>([
    // skip from any step goes off
    ["welcome", { type: "skip" }, "off"],
    ["watch", { type: "skip" }, "off"],
    ["click", { type: "skip" }, "off"],
    ["source", { type: "skip" }, "off"],
    ["closing", { type: "skip" }, "off"],
    ["interrupted", { type: "skip" }, "off"],
    ["more", { type: "skip" }, "off"],
    ["followup", { type: "skip" }, "off"],
    ["off", { type: "skip" }, "off"],
    // start from any step goes to welcome
    ["off", { type: "start" }, "welcome"],
    ["welcome", { type: "start" }, "welcome"],
    ["watch", { type: "start" }, "welcome"],
    ["closing", { type: "start" }, "welcome"],
    // asked
    ["welcome", { type: "asked" }, "watch"],
    ["click", { type: "asked" }, "watch"],
    ["source", { type: "asked" }, "watch"],
    ["interrupted", { type: "asked" }, "watch"],
    // settled with a verified citation
    ["watch", { type: "settled", hasVerified: true }, "click"],
    // settled without one
    ["watch", { type: "settled", hasVerified: false }, "closing"],
    // errored
    ["watch", { type: "errored" }, "interrupted"],
    ["watch", { type: "citationClicked" }, "source"],
    // citation clicked
    ["click", { type: "citationClicked" }, "source"],
    // next
    ["source", { type: "next" }, "more"],
    ["more", { type: "followUp" }, "followup"],
    ["followup", { type: "settled", hasVerified: true }, "closing"],
    ["followup", { type: "settled", hasVerified: false }, "closing"],
    ["followup", { type: "errored" }, "interrupted"],
    ["more", { type: "start" }, "welcome"],
    ["followup", { type: "start" }, "welcome"],
    // finish
    ["more", { type: "finish" }, "off"],
    ["closing", { type: "finish" }, "off"],
  ])("%s + %j -> %s", (from, event, to) => {
    expect(reduceTour(from, event)).toBe(to);
  });

  test("settled without a verified citation goes to closing", () => {
    expect(reduceTour("watch", { type: "settled", hasVerified: false })).toBe(
      "closing",
    );
  });

  test("off ignores every event except start", () => {
    const events: TourEvent[] = [
      { type: "asked" },
      { type: "settled", hasVerified: true },
      { type: "settled", hasVerified: false },
      { type: "errored" },
      { type: "citationClicked" },
      { type: "next" },
      { type: "followUp" },
      { type: "finish" },
    ];
    for (const e of events) expect(reduceTour("off", e)).toBe("off");
  });

  test("unlisted transitions leave the step unchanged", () => {
    expect(reduceTour("welcome", { type: "finish" })).toBe("welcome");
    expect(reduceTour("closing", { type: "citationClicked" })).toBe("closing");
  });

  test("followup ignores citationClicked and next", () => {
    expect(reduceTour("followup", { type: "citationClicked" })).toBe("followup");
    expect(reduceTour("followup", { type: "next" })).toBe("followup");
  });
});

describe("hasSeenTour / markTourSeen", () => {
  test("is false before anything is marked, true after", () => {
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: fakeStorage(),
    });
    expect(hasSeenTour()).toBe(false);
    markTourSeen();
    expect(hasSeenTour()).toBe(true);
    expect(localStorage.getItem(KEY)).not.toBeNull();
  });

  test("hasSeenTour is false and markTourSeen does not throw when localStorage throws", () => {
    throwingStorage();
    expect(hasSeenTour()).toBe(false);
    expect(() => markTourSeen()).not.toThrow();
  });
});

describe("firstVerifiedMarker", () => {
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

  function map(...cs: Citation[]): Map<number, Citation> {
    return new Map(cs.map((c) => [c.marker, c]));
  }

  test("returns the lowest verified marker", () => {
    expect(
      firstVerifiedMarker(map(citation(3), citation(1), citation(2))),
    ).toBe(1);
  });

  test("skips unverified and unattributable citations", () => {
    expect(
      firstVerifiedMarker(
        map(
          citation(1, { verified: false }),
          citation(2, { accession: "" }),
          citation(4),
        ),
      ),
    ).toBe(4);
  });

  test("returns null when nothing qualifies", () => {
    expect(firstVerifiedMarker(new Map())).toBeNull();
    expect(
      firstVerifiedMarker(map(citation(1, { verified: false }))),
    ).toBeNull();
  });
});

test("askEvent finishes the tour from more and closing, a plain ask elsewhere", () => {
  const finish: TourStep[] = ["more", "closing"];
  const rest: TourStep[] = [
    "welcome", "watch", "click", "source", "followup", "interrupted", "off",
  ];
  for (const s of finish) expect(askEvent(s)).toEqual({ type: "finish" });
  for (const s of rest) expect(askEvent(s)).toEqual({ type: "asked" });
});
