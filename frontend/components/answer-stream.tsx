"use client";

import { CitationChip } from "@/components/citation-chip";
import type { AnswerState } from "@/lib/answer";
import { splitOnMarkers, splitParagraphs } from "@/lib/markers";
import type { Citation } from "@/lib/types";

function Dot({ delay }: { delay: string }) {
  return (
    <span
      className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-500"
      style={{ animationDelay: delay }}
    />
  );
}

export function AnswerStream({
  state,
  onSelect,
  emphasizeMarker = null,
}: {
  state: AnswerState;
  onSelect: (citation: Citation) => void;
  emphasizeMarker?: number | null;
}) {
  if (state.status === "idle") {
    return <p className="text-slate-500">Ask a question about a filing.</p>;
  }
  if (state.status === "done" && state.chunksRetrieved === 0) {
    return <p className="text-slate-500">No matching filings.</p>;
  }
  // The backend runs detection + retrieval + generation before the first
  // token, so an empty streaming turn means "still working", not "empty
  // answer".
  if (state.status === "streaming" && state.prose === "") {
    return (
      <div
        className="flex items-center gap-1.5 py-1"
        role="status"
        aria-label="Loading answer"
      >
        <Dot delay="-0.3s" />
        <Dot delay="-0.15s" />
        <Dot delay="0s" />
      </div>
    );
  }

  return (
    <div>
      {state.errorMessage && (
        <p className="mb-3 rounded bg-red-950 p-2 text-sm text-red-300">
          {state.errorMessage}
        </p>
      )}
      {state.notice && (
        <p className="mb-3 rounded bg-amber-950 p-2 text-sm text-amber-300">
          {state.notice}
        </p>
      )}
      {/* One block per paragraph with a small gap, rather than the blank
          line that pre-wrap would render between them. pre-wrap stays for
          single line breaks inside a paragraph (lists). */}
      <div className="space-y-[0.6em] text-sm leading-[1.55]">
        {splitParagraphs(state.prose).map((paragraph, p) => (
          <p key={p} className="whitespace-pre-wrap">
            {splitOnMarkers(paragraph).map((segment, index) =>
              typeof segment === "string" ? (
                <span key={index}>{segment}</span>
              ) : (
                <CitationChip
                  key={index}
                  marker={segment.marker}
                  citation={state.citations.get(segment.marker)}
                  onSelect={onSelect}
                  emphasized={emphasizeMarker === segment.marker}
                />
              ),
            )}
          </p>
        ))}
      </div>
    </div>
  );
}
