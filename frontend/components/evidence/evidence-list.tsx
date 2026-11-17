"use client";

import type { ReactNode } from "react";

import { EvidenceCard } from "@/components/evidence/evidence-card";
import type { Citation } from "@/lib/types";

/** The DOM id of a turn's card for one marker, so a chip click can scroll to it. */
export function cardId(turnId: number, marker: number): string {
  return `evidence-${turnId}-${marker}`;
}

/**
 * Every citation of one turn, in marker order, as evidence cards. `coach`
 * renders directly above the selected card (the tutorial's source step).
 */
export function EvidenceList({
  turnId,
  citations,
  selectedMarker,
  onOpenFiling,
  coach,
}: {
  turnId: number;
  citations: Map<number, Citation>;
  selectedMarker: number | null;
  onOpenFiling: (citation: Citation, opener: HTMLElement) => void;
  coach?: ReactNode;
}) {
  const ordered = [...citations.values()].sort((a, b) => a.marker - b.marker);
  if (ordered.length === 0) return null;
  const verified = ordered.filter((c) => c.verified && c.accession !== "").length;

  return (
    <section aria-label="Evidence" className="mt-4 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <h2 className="font-mono text-[10px] tracking-[0.09em] text-slate-400 uppercase">Evidence</h2>
        <span
          className={
            "rounded-full px-2 py-0.5 text-[11px] " +
            (verified === ordered.length ? "bg-green-950 text-green-300" : "bg-red-950 text-red-300")
          }
        >
          {verified} of {ordered.length} {ordered.length === 1 ? "quote" : "quotes"} verified
        </span>
      </div>
      {ordered.map((citation) => (
        // The id is on the wrapper, so scrolling to a selected card brings the
        // tutorial card above it on screen too.
        <div key={citation.marker} id={cardId(turnId, citation.marker)} className="flex scroll-mt-4 flex-col gap-2">
          {citation.marker === selectedMarker && coach}
          <EvidenceCard
            citation={citation}
            selected={citation.marker === selectedMarker}
            onOpenFiling={onOpenFiling}
          />
        </div>
      ))}
    </section>
  );
}
