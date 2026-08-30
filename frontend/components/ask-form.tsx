"use client";

import { useEffect, useState } from "react";

import { fetchCompanies } from "@/lib/api";
import type { AskFilters } from "@/lib/api";
import type { Company } from "@/lib/types";

// Matches MAX_COMPANIES in backend/src/api/detect.py.
const MAX_TICKERS = 4;

export function AskForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (question: string, filters: AskFilters) => void;
}) {
  const [question, setQuestion] = useState("");
  const [tickers, setTickers] = useState<string[]>([]);
  const [formType, setFormType] = useState("");
  const [companies, setCompanies] = useState<Company[]>([]);

  useEffect(() => {
    // A failed company list only costs the filter dropdown, so it must not
    // block asking questions.
    fetchCompanies()
      .then(setCompanies)
      .catch(() => setCompanies([]));
  }, []);

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
    <form
      className="mb-6 flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (!question.trim()) return;
        onSubmit(question.trim(), {
          tickers: tickers.length > 0 ? tickers : undefined,
          form_type: formType || undefined,
        });
      }}
    >
      <input
        aria-label="Question"
        className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-blue-600 focus:outline-none"
        placeholder="What were Apple's total net sales in fiscal 2024?"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
      />
      <div className="flex flex-wrap items-center gap-1">
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
                  ? "border-blue-600 bg-blue-700 text-white"
                  : atCap
                    ? "cursor-not-allowed border-slate-800 bg-slate-900 text-slate-600"
                    : "border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-500")
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
            className="ml-1 text-xs text-slate-500 underline hover:text-slate-300"
          >
            Clear
          </button>
        )}
      </div>
      <div className="flex gap-2">
        <select
          aria-label="Form type"
          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs text-slate-300"
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
          className="rounded bg-blue-700 px-4 py-1 text-sm text-white hover:bg-blue-600 disabled:bg-slate-800 disabled:text-slate-500"
        >
          {disabled ? "Asking…" : "Ask"}
        </button>
      </div>
    </form>
  );
}
