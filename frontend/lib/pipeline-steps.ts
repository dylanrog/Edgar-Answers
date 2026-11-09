import type { AnswerState } from "./answer";
import type { FilingRef } from "./types";

export type StepStatus = "pending" | "running" | "done" | "failed";
export type PanelLine = {
  key: string;
  status: StepStatus;
  text: string;
  detail?: string;
};

/** Pause between revealing one quote check and the next. Only the reveal is
 *  paced; every other line ticks when its server event arrives. */
export const REVEAL_INTERVAL_MS = 400;

const label = (f: FilingRef) =>
  `${f.ticker} ${f.form_type} ${f.filing_date.slice(0, 4)}`;

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function buildPanel(
  state: AnswerState,
  names: Record<string, string>,
  revealed: number,
): PanelLine[] {
  const { targets, retrieved, quotes } = state.progress;
  const lines: PanelLine[] = [];

  if (targets) {
    const who =
      targets.tickers.length === 0
        ? "Searching every company"
        : `Understood the question: ${targets.tickers.map((t) => names[t] ?? t).join(", ")}` +
          (targets.filings.length > 0
            ? ` · ${targets.filings.map(label).join(", ")}`
            : "");
    lines.push({ key: "targets", status: "done", text: who, detail: "company and period detection" });
  } else {
    lines.push({
      key: "targets",
      status: "running",
      text: "Understanding the question",
      detail: "company and period detection",
    });
  }

  if (targets) {
    if (retrieved) {
      lines.push({
        key: "search",
        status: "done",
        text: `Searched the filings: found ${plural(retrieved.passages, "passage")} in ${plural(retrieved.filings.length, "filing")}`,
        detail: `vector + keyword search · ${retrieved.filings.map(label).join(", ")}`,
      });
    } else {
      lines.push({
        key: "search",
        status: "running",
        text: "Searching the filings",
        detail: "vector + keyword search",
      });
    }
  }

  if (retrieved) {
    lines.push({
      key: "write",
      status: quotes !== null ? "done" : "running",
      text: "Writing the answer",
      detail: "Claude Haiku, told to quote its sources word for word",
    });
  }

  if (quotes !== null) {
    const total = state.citations.size;
    lines.push({
      key: "check",
      status: revealed >= total && state.status !== "streaming" ? "done" : "running",
      text: "Checking each quote against the source",
    });
    const shown = [...state.citations.values()]
      .sort((a, b) => a.marker - b.marker)
      .slice(0, revealed);
    for (const c of shown) {
      if (c.verified) {
        const first = c.sids[0];
        const last = c.sids[c.sids.length - 1];
        lines.push({
          key: `quote-${c.marker}`,
          status: "done",
          text: `Quote ${c.marker} → ${label(c)} ✓ found word for word`,
          detail:
            (first !== undefined ? `sentences ${first}–${last}` : "") +
            (c.cells.length > 0 ? " · table figure" : ""),
        });
      } else {
        lines.push({
          key: `quote-${c.marker}`,
          status: "failed",
          text: `Quote ${c.marker} ✗ not found in the source`,
        });
      }
    }
  }

  if (state.status === "error") {
    const running = lines.find((l) => l.status === "running");
    if (running) running.status = "failed";
  }
  return lines;
}

export function isSettled(state: AnswerState, revealed: number): boolean {
  return (
    (state.status === "done" || state.status === "error") &&
    revealed >= state.citations.size
  );
}
