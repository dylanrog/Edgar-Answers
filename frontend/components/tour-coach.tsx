"use client";

import type { ReactNode } from "react";

import { ExampleQuestions } from "@/components/example-questions";
import type { Example } from "@/lib/examples";

const REPO_URL = "https://github.com/dylanrog/Edgar-Answers";

function SkipButton({ onSkip }: { onSkip: () => void }) {
  return (
    <button
      type="button"
      onClick={onSkip}
      className="text-xs text-slate-500 underline hover:text-slate-300"
    >
      Skip tour
    </button>
  );
}

/** Replaces the empty-state text above the form on first visit. */
export function TourWelcome({
  examples,
  onPick,
  onSkip,
  disabled,
}: {
  examples: Example[];
  onPick: (example: Example) => void;
  onSkip: () => void;
  disabled: boolean;
}) {
  return (
    <div className="px-5 pb-4" data-testid="tour-welcome">
      <p className="text-base text-slate-200">
        <strong className="font-semibold text-white">
          Answers from SEC filings, with every quote checked.
        </strong>{" "}
        Pick a question to see how it works.
      </p>
      <div className="mt-3">
        <ExampleQuestions examples={examples} onPick={onPick} disabled={disabled} />
      </div>
      <div className="mt-3">
        <SkipButton onSkip={onSkip} />
      </div>
    </div>
  );
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <div
      role="note"
      data-testid="tour-callout"
      className="mt-3 rounded border border-blue-500/60 bg-blue-950/40 px-3 py-2 text-sm text-slate-200"
    >
      {children}
    </div>
  );
}

type CalloutStep = "watch" | "click" | "source" | "closing" | "interrupted";

/** One inline coach callout. Never an overlay: nothing is covered. */
export function TourCallout({
  step,
  onSkip,
  onNext,
  onFinish,
  onRetry,
  examples = [],
  onPick,
  disabled = false,
}: {
  step: CalloutStep;
  onSkip: () => void;
  onNext?: () => void;
  onFinish?: () => void;
  onRetry?: () => void;
  examples?: Example[];
  onPick?: (example: Example) => void;
  disabled?: boolean;
}) {
  const action =
    "rounded bg-blue-700 px-3 py-1 text-xs font-semibold text-white hover:bg-blue-600";
  const copy = {
    watch:
      "Watch the panel: every quote the model uses is checked against the filing before you see it.",
    click: "Click the highlighted citation to see it in the original filing.",
    source:
      "That's the exact sentence (or figure) in the original filing. The server confirmed the quote word for word.",
    closing:
      "This demo runs on 120 SEC filings from ten companies, but the pipeline underneath (sentence-anchored ingestion, hybrid search, quote verification) works on any document collection.",
    interrupted:
      "That one didn't go through. You can try again or explore on your own.",
  }[step];

  return (
    <Frame>
      <p>{copy}</p>
      {step === "closing" && onPick && (
        <div className="mt-2">
          <ExampleQuestions
            examples={examples.filter((e) => !e.featured)}
            onPick={onPick}
            disabled={disabled}
          />
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-3">
        {step === "source" && (
          <button type="button" onClick={onNext} className={action}>
            Next
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
