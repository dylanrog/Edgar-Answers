"use client";

import { coverageSummary } from "@/lib/coverage";
import type { Company } from "@/lib/types";

/**
 * The first-visit screen (design canvas, round 2, screen 1): what the app is,
 * what it covers -- said once -- and two ways in. No input bar here: the
 * choice is the tour or your own question.
 */
export function Landing({
  companies,
  onTour,
  onSkip,
  disabled,
}: {
  companies: Company[];
  onTour: () => void;
  onSkip: () => void;
  disabled: boolean;
}) {
  const summary = coverageSummary(companies);
  return (
    <main
      data-testid="tour-welcome"
      className="flex min-h-dvh flex-col bg-slate-950 px-6 pb-8 pt-7 text-slate-200"
    >
      <div className="mx-auto flex w-full max-w-md flex-1 flex-col">
        <h1 className="font-mono text-sm font-bold tracking-wide text-slate-100">EDGAR ANSWERS</h1>

        <div className="flex flex-1 flex-col justify-center gap-5 py-8">
          <p className="text-3xl leading-tight font-semibold tracking-tight text-white">
            Ask questions about SEC filings. Every quote is checked.
          </p>
          <p className="text-base leading-relaxed text-slate-300">
            Each answer cites the exact sentence or table row it came from, and that quote is
            matched word for word against the filing before you see it.
          </p>
          {/* Reserved height so the card arriving with /companies doesn't
              move the buttons under a visitor's thumb. */}
          <div className="min-h-24">
            {summary !== null && (
              <div
                data-testid="coverage"
                className="rounded-xl border border-slate-800 bg-slate-900 px-4 py-3.5"
              >
                <p className="text-[15px] leading-normal text-slate-100">{summary}</p>
                <p className="mt-2.5 font-mono text-xs leading-relaxed text-slate-400">
                  {companies.map((c) => c.ticker).join(" · ")}
                </p>
              </div>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <button
            type="button"
            onClick={onTour}
            disabled={disabled}
            className="h-14 rounded-2xl bg-blue-600 text-[17px] font-semibold text-white shadow-[0_10px_30px_rgba(37,99,235,0.35)] hover:bg-blue-500 disabled:bg-slate-800 disabled:text-slate-500"
          >
            Take the 1-minute tour
          </button>
          <button
            type="button"
            onClick={onSkip}
            className="h-14 rounded-2xl border border-slate-700 text-base font-medium text-slate-200 hover:border-slate-500"
          >
            Skip, ask my own question
          </button>
        </div>
      </div>
    </main>
  );
}
