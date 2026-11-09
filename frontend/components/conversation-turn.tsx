"use client";

import { useMemo } from "react";
import type { ReactNode } from "react";

import { AnswerStream } from "@/components/answer-stream";
import { SourcesPanel } from "@/components/sources-panel";
import { UnderTheHood } from "@/components/under-the-hood";
import type { AnswerState } from "@/lib/answer";
import { groupSources } from "@/lib/sources";
import type { Citation } from "@/lib/types";

export function ConversationTurn({
  question,
  state,
  onSelect,
  names,
  expanded,
  onSettled,
  emphasizeMarker,
  coachAbovePanel,
  coachBelowAnswer,
  coachBelowTurn,
}: {
  question: string;
  state: AnswerState;
  onSelect: (citation: Citation) => void;
  names: Record<string, string>;
  expanded: boolean;
  onSettled: () => void;
  emphasizeMarker: number | null;
  // Tour callouts, rendered inline at the three places the coach points.
  coachAbovePanel?: ReactNode;
  coachBelowAnswer?: ReactNode;
  coachBelowTurn?: ReactNode;
}) {
  const groups = useMemo(() => groupSources(state.citations), [state.citations]);

  return (
    <article className="mb-6 border-b border-slate-800 pb-5 last:border-b-0 last:pb-0">
      <div className="border-l-2 border-blue-500 pl-3">
        <p className="text-base font-semibold text-white">{question}</p>
        {state.standaloneQuestion && (
          <p className="mt-1 text-xs text-slate-500">
            Searched for: “{state.standaloneQuestion}”
          </p>
        )}
      </div>
      <div className="mt-2">
        <AnswerStream
          state={state}
          onSelect={onSelect}
          emphasizeMarker={emphasizeMarker}
        />
      </div>
      {coachBelowAnswer}
      {coachAbovePanel}
      <UnderTheHood
        state={state}
        names={names}
        expanded={expanded}
        onSettled={onSettled}
      />
      <SourcesPanel groups={groups} onSelect={onSelect} />
      {coachBelowTurn}
    </article>
  );
}
