"use client";

import { useState } from "react";

import type { AskFilters } from "@/lib/api";
import { coverageCaption } from "@/lib/coverage";
import type { Company } from "@/lib/types";

// Matches MAX_COMPANIES in backend/src/api/detect.py.
const MAX_TICKERS = 4;

/**
 * The evidence layout's input bar, pinned to the bottom like a chat app's
 * (design canvas, round 2). The company filter folds into one button; the
 * caption under it is the only place the chat screen states the coverage.
 */
export function Composer({
  companies,
  disabled,
  onSubmit,
  placeholder,
}: {
  companies: Company[];
  disabled: boolean;
  onSubmit: (question: string, filters: AskFilters) => void;
  placeholder: string;
}) {
  const [question, setQuestion] = useState("");
  const [tickers, setTickers] = useState<string[]>([]);
  const [formType, setFormType] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const caption = coverageCaption(companies);

  function toggleTicker(ticker: string) {
    setTickers((current) => {
      if (current.includes(ticker)) return current.filter((t) => t !== ticker);
      if (current.length >= MAX_TICKERS) return current;
      return [...current, ticker];
    });
  }

  const scope =
    tickers.length === 0
      ? companies.length > 0
        ? `All ${companies.length} companies`
        : "All companies"
      : tickers.join(", ");

  return (
    <div>
      {filtersOpen && (
        <div
          id="composer-filters"
          className="mb-2 rounded-2xl border border-ledger-700 bg-ledger-900 p-3"
        >
          <p className="mb-2 text-xs text-ledger-400">Limit to up to {MAX_TICKERS} companies</p>
          <div className="flex flex-wrap gap-1.5">
            {companies.map((company) => {
              const selected = tickers.includes(company.ticker);
              const atCap = !selected && tickers.length >= MAX_TICKERS;
              return (
                <button
                  key={company.cik}
                  type="button"
                  aria-pressed={selected}
                  disabled={atCap}
                  onClick={() => toggleTicker(company.ticker)}
                  className={
                    "min-h-9 rounded-full border px-3 font-mono text-xs " +
                    (selected
                      ? "border-mint-500 bg-mint-700 text-white"
                      : atCap
                        ? "cursor-not-allowed border-ledger-800 text-ledger-600"
                        : "border-ledger-700 text-ledger-200 hover:border-ledger-500")
                  }
                >
                  {company.ticker}
                </button>
              );
            })}
          </div>
          <div className="mt-3 flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-ledger-400">
              Form
              <select
                aria-label="Form type"
                value={formType}
                onChange={(event) => setFormType(event.target.value)}
                className="min-h-9 rounded-lg border border-ledger-700 bg-ledger-950 px-2 font-mono text-xs text-ledger-200"
              >
                <option value="">All forms</option>
                <option value="10-K">10-K</option>
                <option value="10-Q">10-Q</option>
              </select>
            </label>
            {(tickers.length > 0 || formType !== "") && (
              <button
                type="button"
                onClick={() => {
                  setTickers([]);
                  setFormType("");
                }}
                className="text-xs text-ledger-400 underline hover:text-ledger-200"
              >
                Clear
              </button>
            )}
          </div>
        </div>
      )}

      <form
        className="flex flex-col gap-2 rounded-[22px] border border-ledger-700 bg-ledger-900 py-2.5 pl-4 pr-2.5"
        onSubmit={(event) => {
          event.preventDefault();
          if (!question.trim()) return;
          onSubmit(question.trim(), {
            tickers: tickers.length > 0 ? tickers : undefined,
            form_type: formType || undefined,
          });
          setQuestion("");
          setFiltersOpen(false);
        }}
      >
        <input
          aria-label="Question"
          className="bg-transparent py-1.5 text-base text-ledger-100 placeholder:text-ledger-500 focus:outline-none"
          placeholder={placeholder}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-expanded={filtersOpen}
            aria-controls="composer-filters"
            onClick={() => setFiltersOpen((open) => !open)}
            className={
              "flex min-h-9 items-center gap-1.5 rounded-full border px-3 text-[13px] " +
              (tickers.length > 0 || formType !== ""
                ? "border-mint-500 text-mint-200"
                : "border-ledger-700 text-ledger-300 hover:border-ledger-500")
            }
          >
            {scope}
            {formType !== "" ? ` · ${formType}` : ""}
            <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d={filtersOpen ? "m18 15-6-6-6 6" : "m6 9 6 6 6-6"} />
            </svg>
          </button>
          <button
            type="submit"
            aria-label="Ask"
            disabled={disabled}
            className="ml-auto flex h-11 w-11 items-center justify-center rounded-full bg-mint-400 text-ledger-950 hover:bg-mint-300 disabled:bg-ledger-700 disabled:text-ledger-400"
          >
            <svg aria-hidden width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 19V5" />
              <path d="m5 12 7-7 7 7" />
            </svg>
          </button>
        </div>
      </form>
      {caption !== null && (
        <p data-testid="coverage-caption" className="mt-2 text-center text-xs text-ledger-400">
          {caption}
        </p>
      )}
    </div>
  );
}
