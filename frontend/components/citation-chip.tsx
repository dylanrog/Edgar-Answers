"use client";

import type { Citation } from "@/lib/types";

/**
 * Three states, because the backend sends every token before any citation:
 *  - undefined citation -> inert text, the answer is still streaming
 *  - verified           -> clickable
 *  - unverified         -> visible badge, deliberately NOT clickable
 * A failed citation is never dropped silently (design §6.3).
 */
export function CitationChip({
  marker,
  citation,
  onSelect,
  emphasized = false,
}: {
  marker: number;
  citation: Citation | undefined;
  onSelect: (citation: Citation) => void;
  emphasized?: boolean;
}) {
  if (citation === undefined) {
    return <span className="text-ledger-600">[{marker}]</span>;
  }
  if (!citation.verified) {
    return (
      <span
        className="mx-0.5 rounded bg-red-950 px-1 font-mono text-[10px] leading-none text-red-300"
        title={`Unverified: "${citation.quote}"`}
      >
        [{marker}] unverified
      </span>
    );
  }
  // A compact inline badge: leading-none and no vertical padding, so a chip
  // never makes its line taller than the prose around it. On touch screens an
  // invisible ::after widens the hit area to at least 40 px without moving
  // anything.
  return (
    <button
      type="button"
      onClick={() => onSelect(citation)}
      className={
        "relative mx-0.5 inline-block rounded bg-mint-900 px-1 align-baseline font-mono text-[10px] leading-none text-mint-200 hover:bg-mint-800 " +
        "pointer-coarse:after:absolute pointer-coarse:after:left-1/2 pointer-coarse:after:top-1/2 pointer-coarse:after:h-10 pointer-coarse:after:w-10 pointer-coarse:after:min-w-full pointer-coarse:after:-translate-x-1/2 pointer-coarse:after:-translate-y-1/2 pointer-coarse:after:content-['']" +
        (emphasized ? " ring-2 ring-amber-400" : "")
      }
      title={citation.quote}
      data-emphasized={emphasized || undefined}
    >
      [{marker}]
      {emphasized && (
        // The ping ring is the "click me" signal; under reduced motion it
        // stays still and the steady ring on the chip carries the emphasis.
        <span
          aria-hidden="true"
          data-testid="chip-ping"
          className="pointer-events-none absolute -inset-1 rounded ring-2 ring-amber-400 motion-safe:animate-ping"
        />
      )}
    </button>
  );
}
