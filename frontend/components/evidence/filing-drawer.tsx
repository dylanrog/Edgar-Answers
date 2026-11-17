"use client";

import { useEffect, useMemo, useRef } from "react";

import { FilingViewer } from "@/components/filing-viewer";
import { initialTabState, openTab } from "@/lib/tabs";
import type { Citation } from "@/lib/types";

/**
 * The whole filing, on request (spec 2026-11-17 §4.1): the existing viewer,
 * highlight and all, in a drawer over the page. Right-hand and 60% wide on a
 * wide screen, the full screen on a phone. Closes with ✕, the backdrop or
 * Escape, and hands focus back to the button that opened it.
 */
export function FilingDrawer({
  citation,
  opener,
  onClose,
}: {
  citation: Citation;
  opener: HTMLElement | null;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, [onClose, opener]);

  // Memoized: the viewer re-applies (and re-scrolls) its highlight whenever
  // these change identity, and the page re-renders on every streamed token.
  const tabs = useMemo(() => openTab(initialTabState, citation.accession), [citation.accession]);
  const highlights = useMemo(
    () => ({ [citation.accession]: { sids: citation.sids, cells: citation.cells } }),
    [citation],
  );

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label="Full filing">
      <button
        type="button"
        tabIndex={-1}
        aria-label="Close the filing"
        onClick={onClose}
        className="absolute inset-0 h-full w-full cursor-default bg-black/60"
      />
      <div className="absolute inset-y-0 right-0 flex w-full flex-col border-l border-slate-700 bg-slate-950 shadow-2xl md:w-[60%]">
        <div className="flex items-center gap-3 border-b border-slate-800 px-4 py-3">
          <div className="min-w-0 flex-1">
            <p className="font-mono text-sm font-semibold text-slate-100">
              {citation.ticker} · {citation.form_type}
            </p>
            <p className="text-xs text-slate-400">filed {citation.filing_date}</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="min-h-10 rounded-md border border-slate-600 px-3 text-sm text-slate-200 hover:border-slate-400"
          >
            Close
          </button>
        </div>
        <div className="min-h-0 flex-1">
          <FilingViewer tabs={tabs} highlights={highlights} />
        </div>
      </div>
    </div>
  );
}
