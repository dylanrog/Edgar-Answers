"use client";

import type { SourceGroup } from "@/lib/sources";
import type { Citation } from "@/lib/types";

export function SourcesPanel({
  groups,
  onSelect,
}: {
  groups: SourceGroup[];
  onSelect: (citation: Citation) => void;
}) {
  if (groups.length === 0) return null;

  const citationCount = groups.reduce((n, g) => n + g.citations.length, 0);

  return (
    <section className="mt-6">
      <h2 className="font-mono text-[10px] tracking-[0.09em] text-ledger-500 uppercase">
        Sources · {groups.length} {groups.length === 1 ? "filing" : "filings"} ·{" "}
        {citationCount} {citationCount === 1 ? "citation" : "citations"}
      </h2>

      <ul className="mt-2 space-y-1">
        {groups.map((group) => (
          <li
            key={group.accession || "unattributable"}
            className={
              group.openable
                ? "border-l-2 border-mint-500 bg-ledger-900 px-3 py-2"
                : "border-l-2 border-red-500 bg-ledger-900 px-3 py-2"
            }
          >
            <div className="flex items-baseline justify-between text-xs">
              {group.accession === "" ? (
                <span className="font-mono font-bold text-ledger-300">
                  unattributable
                </span>
              ) : (
                <span className="font-mono font-bold text-ledger-200">
                  {group.ticker}{" "}
                  <span className="font-normal text-ledger-500">
                    {group.form_type} {group.filing_date}
                  </span>
                </span>
              )}
              <span
                className={group.verifiedCount > 0 ? "text-lime-500" : "text-red-400"}
              >
                {group.verifiedCount > 0 ? `${group.verifiedCount} ✓` : "unverified"}
              </span>
            </div>

            <ul className="mt-1.5 space-y-1 border-l border-ledger-800 pl-2">
              {group.citations.map((citation) => (
                <li key={citation.marker} className="text-[11px] leading-snug">
                  {citation.verified ? (
                    <button
                      type="button"
                      onClick={() => onSelect(citation)}
                      className="text-left hover:text-ledger-100"
                    >
                      <span className="mr-1 rounded bg-mint-900 px-1 font-mono text-mint-200">
                        {citation.marker}
                      </span>
                      <span className="text-ledger-400">“{citation.quote}”</span>
                    </button>
                  ) : (
                    <span>
                      <span className="mr-1 rounded bg-red-950 px-1 font-mono text-red-300">
                        {citation.marker}
                      </span>
                      <span className="text-ledger-500">
                        quote did not match source text
                      </span>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
}
