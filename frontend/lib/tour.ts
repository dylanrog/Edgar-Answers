import type { Citation } from "./types";

export type TourStep =
  "welcome" | "watch" | "click" | "source" | "closing" | "interrupted" | "off";

export type TourEvent =
  | { type: "start" }
  | { type: "skip" }
  | { type: "asked" }
  | { type: "settled"; hasVerified: boolean }
  | { type: "errored" }
  | { type: "citationClicked" }
  | { type: "next" }
  | { type: "finish" };

const SEEN_KEY = "edgar-answers.tour-seen";

/**
 * Pure transition function. Events not listed for a step leave it unchanged;
 * `skip` ends the tour from anywhere and `start` restarts it from anywhere.
 */
export function reduceTour(step: TourStep, event: TourEvent): TourStep {
  if (event.type === "skip") return "off";
  if (event.type === "start") return "welcome";

  if (step === "off") return "off";

  if (
    event.type === "asked" &&
    (step === "welcome" ||
      step === "click" ||
      step === "source" ||
      step === "interrupted")
  ) {
    return "watch";
  }
  if (step === "watch" && event.type === "settled") {
    return event.hasVerified ? "click" : "closing";
  }
  if (step === "watch" && event.type === "errored") return "interrupted";
  // Clicking during the paced reveal: the highlight is already showing.
  if ((step === "watch" || step === "click") && event.type === "citationClicked") {
    return "source";
  }
  if (step === "source" && event.type === "next") return "closing";
  if (step === "closing" && event.type === "finish") return "off";
  return step;
}

/**
 * What asking a question means for the tour: from `closing` it is the
 * visitor leaving the tour for their own exploration, so it finishes it.
 */
export function askEvent(step: TourStep): TourEvent {
  return step === "closing" ? { type: "finish" } : { type: "asked" };
}

/** True once the tour has been marked seen. Storage failures read as unseen. */
export function hasSeenTour(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) !== null;
  } catch {
    return false;
  }
}

/** Records that the tour was seen. A storage failure is swallowed. */
export function markTourSeen(): void {
  try {
    localStorage.setItem(SEEN_KEY, "1");
  } catch {
    // Storage disabled: the tour may show again next visit, which is harmless.
  }
}

/**
 * The lowest marker whose citation is verified and attributable to a filing
 * (non-empty accession). Null when no citation qualifies.
 */
export function firstVerifiedMarker(
  citations: Map<number, Citation>,
): number | null {
  let best: number | null = null;
  for (const c of citations.values()) {
    if (!c.verified || c.accession === "") continue;
    if (best === null || c.marker < best) best = c.marker;
  }
  return best;
}
