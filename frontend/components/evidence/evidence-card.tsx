"use client";

import { useEffect, useRef, useState } from "react";

import { buildExcerpt } from "@/lib/excerpt";
import { loadParsedFiling } from "@/lib/filing-cache";
import { applyHighlight, centreInPane, keepCentred } from "@/lib/highlight";
import type { Citation } from "@/lib/types";

type Status = "loading" | "ready" | "missing" | "error";

/**
 * One citation's proof (spec 2026-11-17 §4): the cited table, or the cited
 * sentence with a neighbour either side, cut from the real filing and
 * highlighted with the same code as the full viewer. An unverified citation
 * shows its quote struck through and a red status instead -- never dropped.
 */
export function EvidenceCard({
  citation,
  selected,
  onOpenFiling,
}: {
  citation: Citation;
  selected: boolean;
  onOpenFiling: (citation: Citation, opener: HTMLElement) => void;
}) {
  const openable = citation.verified && citation.accession !== "";

  return (
    <article
      data-marker={citation.marker}
      aria-current={selected ? "true" : undefined}
      className={
        "overflow-hidden rounded-lg border bg-ledger-900 " +
        (selected ? "border-mint-500 ring-1 ring-mint-500/50" : openable ? "border-ledger-700" : "border-red-900")
      }
    >
      <header className="flex flex-wrap items-baseline gap-x-2 gap-y-1 border-b border-ledger-800 px-3 py-2 text-xs">
        <span
          className={
            "rounded px-1.5 font-mono font-semibold " +
            (openable ? "bg-mint-900 text-mint-100" : "bg-red-950 text-red-300")
          }
        >
          {citation.marker}
        </span>
        {citation.accession === "" ? (
          <span className="font-mono text-ledger-300">unattributable</span>
        ) : (
          <>
            <span className="font-mono font-semibold text-ledger-100">
              {citation.ticker} · {citation.form_type}
            </span>
            <span className="text-ledger-400">filed {citation.filing_date}</span>
          </>
        )}
        <span className={"ml-auto " + (openable ? "text-lime-400" : "text-red-400")}>
          {openable ? "Exact match in the filing" : "Quote not found in the filing"}
        </span>
      </header>

      {openable ? (
        // Keyed on what the excerpt shows: a different citation mounts a fresh
        // excerpt, so its quote never sits over the last citation's table.
        <Excerpt
          key={`${citation.accession}|${citation.sids.join(",")}|${citation.cells.map((c) => `${c.sid}:${c.cell}`).join(",")}`}
          citation={citation}
        />
      ) : (
        <p className="px-3 py-3 text-sm text-ledger-500 line-through decoration-red-500/70">
          “{citation.quote}”
        </p>
      )}

      {openable && (
        <footer className="flex justify-end border-t border-ledger-800 px-3 py-1.5">
          <button
            type="button"
            onClick={(event) => onOpenFiling(citation, event.currentTarget)}
            className="min-h-9 text-xs text-mint-300 underline hover:text-mint-200"
          >
            Open full filing
          </button>
        </footer>
      )}
    </article>
  );
}

function Excerpt({ citation }: { citation: Citation }) {
  const [status, setStatus] = useState<Status>("loading");
  const boxRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const citedRef = useRef<Element | null>(null);
  const sidsKey = citation.sids.join(",");
  const cellsKey = citation.cells.map((c) => `${c.sid}:${c.cell}`).join(",");

  useEffect(() => {
    let cancelled = false;
    loadParsedFiling(citation.accession)
      .then(({ content }) => {
        const target = contentRef.current;
        if (cancelled || target === null) return;
        const excerpt = buildExcerpt(content, citation.sids);
        if (excerpt === null) {
          setStatus("missing");
          return;
        }
        // Injected imperatively, as in the full viewer: React never owns these
        // children, so a re-render can't wipe the highlight marks.
        target.replaceChildren(excerpt);
        citedRef.current = applyHighlight(target, citation.sids, citation.cells);
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
    // The keys stand in for the arrays, which are new objects on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [citation.accession, sidsKey, cellsKey]);

  // Centred after the commit that removed the loading line, so the offsets are
  // measured on the final layout. Scrolls only the excerpt box, never the page.
  useEffect(() => {
    const box = boxRef.current;
    const cited = citedRef.current;
    if (status !== "ready" || box === null || cited === null) return;
    centreInPane(box, cited);
    return keepCentred(box, cited, [box, contentRef.current ?? box]);
  }, [status]);

  return (
    <div
      ref={boxRef}
      className="filing-pane evidence-excerpt max-h-64 overflow-auto bg-ledger-950 px-3 py-2"
    >
      {status === "loading" && <p className="text-xs text-ledger-500">Loading the filing…</p>}
      {status === "error" && <p className="text-xs text-red-400">The filing could not be loaded.</p>}
      {status === "missing" && (
        <p className="text-sm text-ledger-400">
          “{citation.quote}”{" "}
          <span className="text-xs text-ledger-500">(open the full filing to see it in place)</span>
        </p>
      )}
      <div ref={contentRef} className="filing-html" />
    </div>
  );
}
