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
    return <span className="text-slate-600">[{marker}]</span>;
  }
  if (!citation.verified) {
    return (
      <span
        className="mx-0.5 rounded bg-red-950 px-1 font-mono text-[10px] text-red-300"
        title={`Unverified: "${citation.quote}"`}
      >
        [{marker}] unverified
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onSelect(citation)}
      className={
        "mx-0.5 rounded bg-blue-900 px-1 font-mono text-[10px] text-blue-200 hover:bg-blue-800" +
        (emphasized ? " animate-pulse ring-2 ring-blue-400" : "")
      }
      title={citation.quote}
    >
      [{marker}]
    </button>
  );
}
