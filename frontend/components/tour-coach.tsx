"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import type { ReactNode } from "react";

import { TUTORIAL } from "@/lib/tutorial";

const REPO_URL = "https://github.com/dylanrog/Edgar-Answers";

function SkipButton({ onSkip }: { onSkip: () => void }) {
  return (
    <button
      type="button"
      onClick={onSkip}
      className="text-xs text-slate-500 underline hover:text-slate-300"
    >
      Skip tutorial
    </button>
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
      <p className="text-base text-slate-200">
        <strong className="font-semibold text-white">
          Answers from SEC filings, with every quote checked.
        </strong>{" "}
        Here&apos;s a question to show you how it works:
      </p>
      <div className="mt-3 rounded border border-blue-500/60 bg-blue-950/40 p-3">
        <p className="text-sm text-slate-100">&ldquo;{TUTORIAL.question}&rdquo;</p>
        <button
          type="button"
          onClick={onAsk}
          disabled={disabled}
          className="mt-3 rounded-md bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-blue-900/50 hover:bg-blue-500 disabled:bg-slate-800 disabled:text-slate-500"
        >
          Ask this question
        </button>
        <p className="mt-2 text-xs text-slate-400">Takes under a minute.</p>
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
      className="mt-3 rounded border border-blue-500/60 bg-blue-950/40 px-3 py-2 text-sm text-slate-200"
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
  disabled = false,
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
  disabled?: boolean;
}) {
  const coarse = useCoarsePointer();
  const action =
    "rounded bg-blue-700 px-3 py-1 text-xs font-semibold text-white hover:bg-blue-600";
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
    interrupted:
      "That one didn't go through. You can try again or explore on your own.",
  }[step];

  return (
    <Frame scrollIntoView={step === "more" || step === "closing" || step === "interrupted"}>
      <p>{copy}</p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
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
        {step === "interrupted" && (
          <button type="button" onClick={onRetry} className={action}>
            Try again
          </button>
        )}
        {step === "closing" && (
          <>
            <a
              href={REPO_URL}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-blue-300 underline hover:text-blue-200"
            >
              See the code on GitHub
            </a>
            <button type="button" onClick={onFinish} className={action}>
              Done
            </button>
          </>
        )}
        {step !== "closing" && <SkipButton onSkip={onSkip} />}
      </div>
    </Frame>
  );
}
