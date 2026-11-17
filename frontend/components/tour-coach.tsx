"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import type { ReactNode } from "react";

import { TUTORIAL } from "@/lib/tutorial";

function SkipButton({ onSkip, label = "Skip tutorial", className }: { onSkip: () => void; label?: string; className?: string }) {
  return (
    <button
      type="button"
      onClick={onSkip}
      className={className ?? "text-xs text-ledger-500 underline hover:text-ledger-300"}
    >
      {label}
    </button>
  );
}

/** The dock's "Step n of 5". Follow-up and its answer share step 4; an
 *  interruption has no number. */
const DOCK_STEP: Partial<Record<CalloutStep, number>> = {
  watch: 1,
  click: 2,
  source: 3,
  more: 4,
  followup: 4,
  closing: 5,
};
const DOCK_STEPS = 5;

/** The evidence layout's tutorial panel: it sits where the input bar does and
 *  takes its place while the tour runs (design canvas, round 2, idea 2), so
 *  the mode can't be missed and the input coming back means "your turn". */
function DockFrame({ step, children }: { step: CalloutStep; children: ReactNode }) {
  const n = DOCK_STEP[step];
  return (
    <section
      aria-label="Tutorial"
      data-testid="tour-callout"
      className="rounded-2xl border-[1.5px] border-mint-500 bg-mint-950 px-4 pb-3 pt-3 text-ledger-100 shadow-[0_-12px_32px_rgba(2,6,23,0.9)]"
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="rounded-full bg-mint-400 px-2 py-0.5 text-[11px] font-bold tracking-[0.08em] text-ledger-950 uppercase">
          Tutorial
        </span>
        {n !== undefined && (
          <>
            <span className="text-xs text-mint-200">
              Step {n} of {DOCK_STEPS}
            </span>
            <span aria-hidden className="ml-1 flex gap-1">
              {Array.from({ length: DOCK_STEPS }, (_, i) => (
                <span
                  key={i}
                  className={"h-1.5 w-1.5 rounded-full " + (i < n ? "bg-mint-300" : "bg-mint-800")}
                />
              ))}
            </span>
          </>
        )}
      </div>
      {children}
    </section>
  );
}

const COARSE = "(pointer: coarse)";

/** True on a touch-first device. False on the server and in the first client
 *  render, so hydration matches the prerendered HTML. */
function useCoarsePointer(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const query = window.matchMedia(COARSE);
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    },
    () => window.matchMedia(COARSE).matches,
    () => false,
  );
}

/** The first-visit welcome card. It renders above the form, in place of the
 *  empty-state text that otherwise sits below it. */
export function TourWelcome({
  onAsk,
  onSkip,
  disabled,
}: {
  onAsk: () => void;
  onSkip: () => void;
  disabled: boolean;
}) {
  return (
    <div className="px-5 pb-4" data-testid="tour-welcome">
      <p className="text-base text-ledger-200">
        <strong className="font-semibold text-white">
          Answers from SEC filings, with every quote checked.
        </strong>{" "}
        Here&apos;s a question to show you how it works:
      </p>
      <div className="mt-3 rounded border border-mint-500/60 bg-mint-950/40 p-3">
        <p className="text-sm text-ledger-100">&ldquo;{TUTORIAL.question}&rdquo;</p>
        <button
          type="button"
          onClick={onAsk}
          disabled={disabled}
          className="mt-3 rounded-md bg-mint-400 px-5 py-2.5 text-sm font-semibold text-ledger-950 shadow-lg shadow-mint-900/50 hover:bg-mint-300 disabled:bg-ledger-800 disabled:text-ledger-500"
        >
          Ask this question
        </button>
        <p className="mt-2 text-xs text-ledger-400">Takes under a minute.</p>
      </div>
      <div className="mt-3">
        <SkipButton onSkip={onSkip} />
      </div>
    </div>
  );
}

function Frame({ children, scrollIntoView = false }: { children: ReactNode; scrollIntoView?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  // On phones the previous card sat in the viewer; the next one renders in
  // the thread, off screen, so bring it into view.
  useEffect(() => {
    if (scrollIntoView) ref.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [scrollIntoView]);
  return (
    <div
      ref={ref}
      role="note"
      data-testid="tour-callout"
      className="mt-3 rounded border border-mint-500/60 bg-mint-950/40 px-3 py-2 text-sm text-ledger-200"
    >
      {children}
    </div>
  );
}

type CalloutStep =
  | "watch"
  | "click"
  | "source"
  | "more"
  | "followup"
  | "closing"
  | "interrupted";

/** One inline coach callout. Never an overlay: nothing is covered. */
export function TourCallout({
  step,
  onSkip,
  onNext,
  onFinish,
  onRetry,
  onFollowUp,
  marker,
  figure = false,
  followUpResolved = false,
  unverified = false,
  outOfCredits = false,
  disabled = false,
  variant = "inline",
}: {
  step: CalloutStep;
  onSkip: () => void;
  onNext?: () => void;
  onFinish?: () => void;
  onRetry?: () => void;
  onFollowUp?: () => void;
  /** The citation marker the click step points at. */
  marker?: number;
  /** The citation (to be) clicked points at a table figure, not a sentence. */
  figure?: boolean;
  /** The follow-up's standalone (resolved) question has arrived. */
  followUpResolved?: boolean;
  /** The latest answer has a citation that failed verification. The model
   *  occasionally fabricates a quote for an aside; the badge is the product
   *  working, so the tutorial explains it rather than leave it unexplained. */
  unverified?: boolean;
  /** The interruption was the API account running out of credits: retrying
   *  can't help, so the card offers no Try again. The answer above already
   *  shows the server's (configurable) message; the card doesn't repeat it. */
  outOfCredits?: boolean;
  disabled?: boolean;
  /** "inline": a card in the thread (the classic layout). "dock": the panel
   *  that takes the input bar's place (the evidence layout). Same words. */
  variant?: "inline" | "dock";
}) {
  const coarse = useCoarsePointer();
  const action =
    variant === "dock"
      ? "min-h-10 rounded-lg bg-mint-400 px-4 text-sm font-semibold text-ledger-950 hover:bg-mint-300 disabled:bg-ledger-700 disabled:text-ledger-400"
      : "rounded bg-mint-700 px-3 py-1 text-xs font-semibold text-white hover:bg-mint-800";
  const copy = {
    watch:
      "Watch the panel: every quote the model uses is checked against the filing before you see it.",
    click: `${coarse ? "Tap" : "Click"} the pulsing [${marker ?? 1}] to see that ${figure ? "figure" : "sentence"} in the original filing.`,
    source: `That's the exact ${figure ? "figure" : "sentence"} in the original filing, highlighted. The server confirmed the quote word for word before showing it.`,
    more: "Want to see one more? Follow-ups remember the conversation, so a short question is enough.",
    followup: followUpResolved
      ? `Follow-ups keep context: "${TUTORIAL.followUp}" was searched as the full question shown under it.`
      : `Asking "${TUTORIAL.followUp}" in the same conversation…`,
    closing:
      "This demo runs on 120 SEC filings from ten companies, but the pipeline underneath (sentence-anchored ingestion, hybrid search, quote verification) works on any document collection.",
    interrupted: outOfCredits
      ? "The demo can't answer questions right now, so the tour stops here."
      : "That one didn't go through. You can try again or explore on your own.",
  }[step];
  const badgeNote =
    unverified && (step === "more" || step === "closing")
      ? " A red “unverified” badge means that quote didn't match the filing word for word, so the app flags it instead of hiding it."
      : "";

  const dock = variant === "dock";
  const body = (
    <>
      <p className={dock ? "text-[15px] leading-snug" : undefined}>
        {copy}
        {badgeNote}
      </p>
      <div className={dock ? "mt-3 flex flex-wrap items-center gap-3" : "mt-2 flex flex-wrap items-center gap-3"}>
        {step === "source" && (
          <button type="button" onClick={onNext} className={action}>
            Next
          </button>
        )}
        {step === "more" && (
          <button type="button" onClick={onFollowUp} disabled={disabled} className={action}>
            Ask &quot;{TUTORIAL.followUp}&quot;
          </button>
        )}
        {step === "interrupted" && !outOfCredits && (
          <button type="button" onClick={onRetry} className={action}>
            Try again
          </button>
        )}
        {step === "closing" && (
          <button type="button" onClick={onFinish} className={action}>
            Done
          </button>
        )}
        {step !== "closing" &&
          (dock ? (
            <SkipButton
              onSkip={onSkip}
              label="Exit tutorial"
              className="min-h-10 text-sm text-mint-200 underline hover:text-white"
            />
          ) : (
            <SkipButton onSkip={onSkip} />
          ))}
      </div>
    </>
  );

  if (dock) return <DockFrame step={step}>{body}</DockFrame>;
  return (
    <Frame scrollIntoView={step === "more" || step === "closing" || step === "interrupted"}>
      {body}
    </Frame>
  );
}
