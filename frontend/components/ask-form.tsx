"use client";

import { useState } from "react";

import type { AskFilters } from "@/lib/api";
import type { Company } from "@/lib/types";

// Matches MAX_COMPANIES in backend/src/api/detect.py.
const MAX_TICKERS = 4;

export function AskForm({
  companies,
  disabled,
  onSubmit,
  docked = false,
  quiet = false,
  placeholder = "e.g. Apple's net sales in fiscal 2024",
  scopeLabel,
}: {
  companies: Company[];
  disabled: boolean;
  onSubmit: (question: string, filters: AskFilters) => void;
  // `docked` styles the form as an always-visible bottom bar (once a
  // conversation is underway); the default is the raised card shown at the
  // top of an empty conversation.
  docked?: boolean;
  // A secondary Ask button, while another call to action (the tutorial's
  // welcome card) is the one primary button on screen. Still fully usable.
  quiet?: boolean;
  placeholder?: string;
  // A visible label in front of the ticker buttons. The evidence layout uses
  // it to say they are the whole corpus, not optional extras.
  scopeLabel?: string;
}) {
  const [question, setQuestion] = useState("");
  const [tickers, setTickers] = useState<string[]>([]);
  const [formType, setFormType] = useState("");

  function toggleTicker(ticker: string) {
    setTickers((current) => {
      if (current.includes(ticker)) {
        return current.filter((selected) => selected !== ticker);
      }
      if (current.length >= MAX_TICKERS) return current;
      return [...current, ticker];
    });
  }

  return (
    <div
      className={
        docked
          ? "border-t border-ledger-700 bg-ledger-900/95 p-4 shadow-[0_-8px_24px_rgba(0,0,0,0.45)] backdrop-blur"
          : "mx-5 mb-6 mt-1 rounded-lg border border-ledger-700 bg-ledger-800/50 p-4 shadow-lg"
      }
    >
      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (!question.trim()) return;
          onSubmit(question.trim(), {
            tickers: tickers.length > 0 ? tickers : undefined,
            form_type: formType || undefined,
          });
          setQuestion("");
        }}
      >
        <input
          aria-label="Question"
          className="rounded-md border border-ledger-600 bg-ledger-950 px-3.5 py-2.5 text-[15px] text-ledger-100 placeholder:text-ledger-600 focus:border-mint-500 focus:outline-none focus:ring-1 focus:ring-mint-500/40"
          placeholder={placeholder}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <div className="flex flex-wrap items-center gap-1">
          {scopeLabel !== undefined && companies.length > 0 && (
            <span className="mr-1 text-xs text-ledger-400">{scopeLabel}</span>
          )}
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
                  "rounded border px-2 py-1 font-mono text-xs transition-colors " +
                  (selected
                    ? "border-mint-600 bg-mint-700 text-white"
                    : atCap
                      ? "cursor-not-allowed border-ledger-800 bg-ledger-900 text-ledger-600"
                      : "border-ledger-700 bg-ledger-900 text-ledger-300 hover:border-ledger-500")
                }
              >
                {company.ticker}
              </button>
            );
          })}
          {tickers.length > 0 && (
            <button
              type="button"
              onClick={() => setTickers([])}
              className="ml-1 text-xs text-ledger-500 underline hover:text-ledger-300"
            >
              Clear
            </button>
          )}
        </div>
        <div className="flex gap-2">
          <select
            aria-label="Form type"
            className="rounded border border-ledger-700 bg-ledger-900 px-2 py-1 font-mono text-xs text-ledger-300"
            value={formType}
            onChange={(event) => setFormType(event.target.value)}
          >
            <option value="">All forms</option>
            <option value="10-K">10-K</option>
            <option value="10-Q">10-Q</option>
          </select>
          <button
            type="submit"
            disabled={disabled}
            className={
              quiet
                ? "rounded-md border border-ledger-600 bg-transparent px-5 py-2 text-sm font-medium text-ledger-300 hover:border-ledger-400 hover:text-white disabled:text-ledger-500"
                : "rounded-md bg-mint-400 px-5 py-2 text-sm font-medium text-ledger-950 hover:bg-mint-300 disabled:bg-ledger-800 disabled:text-ledger-500"
            }
          >
            {disabled ? "Asking…" : "Ask"}
          </button>
        </div>
      </form>
    </div>
  );
}
