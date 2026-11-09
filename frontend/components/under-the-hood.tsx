"use client";

import { useEffect, useRef, useState } from "react";

import type { AnswerState } from "@/lib/answer";
import { REVEAL_INTERVAL_MS, buildPanel, isSettled } from "@/lib/pipeline-steps";
import type { StepStatus } from "@/lib/pipeline-steps";

const GLYPH: Record<StepStatus, string> = {
  pending: "○",
  running: "◐",
  done: "✓",
  failed: "✗",
};

const GLYPH_COLOR: Record<StepStatus, string> = {
  pending: "text-slate-600",
  running: "animate-pulse text-blue-400",
  done: "text-emerald-400",
  failed: "text-red-400",
};

export function UnderTheHood({
  state,
  names,
  expanded,
  onSettled,
}: {
  state: AnswerState;
  names: Record<string, string>;
  expanded: boolean;
  onSettled: () => void;
}) {
  // null until the user toggles; until then the panel follows `expanded`.
  const [manual, setManual] = useState<boolean | null>(null);
  const open = manual ?? expanded;
  const [revealed, setRevealed] = useState(0);
  const settledRef = useRef(false);

  const total = state.citations.size;
  const started =
    state.progress.quotes !== null ||
    state.status === "done" ||
    state.status === "error";

  // Only the per-quote reveal is paced; it starts once the server says it is
  // verifying and stops at the number of citations received.
  useEffect(() => {
    if (!started || revealed >= total) return;
    const id = setTimeout(() => setRevealed((n) => n + 1), REVEAL_INTERVAL_MS);
    return () => clearTimeout(id);
  }, [started, revealed, total]);

  const settled = isSettled(state, revealed);
  useEffect(() => {
    if (settled && !settledRef.current) {
      settledRef.current = true;
      onSettled();
    }
  }, [settled, onSettled]);

  if (state.status === "idle") return null;

  const lines = buildPanel(state, names, revealed);
  const verified = [...state.citations.values()].filter((c) => c.verified).length;
  const passages = state.progress.retrieved?.passages ?? state.chunksRetrieved ?? 0;

  return (
    <div className="mt-3 rounded border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setManual(!open)}
        className="flex w-full items-center justify-between text-left text-slate-400 hover:text-slate-200"
      >
        <span>
          {open
            ? "Under the hood"
            : `Under the hood: ${passages} passages · ${verified}/${total} quotes verified${state.status === "error" ? " · stopped with an error" : ""}`}
        </span>
        <span aria-hidden>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <ul className="mt-2 space-y-1.5" aria-label="Pipeline steps">
          {lines.map((line) => (
            <li key={line.key} className="flex gap-2">
              <span className={`w-3 shrink-0 ${GLYPH_COLOR[line.status]}`} aria-hidden>
                {GLYPH[line.status]}
              </span>
              <span className="text-slate-300">
                {line.text}
                {line.detail && (
                  <span className="block text-slate-500">{line.detail}</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
