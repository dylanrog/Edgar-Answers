"use client";

import { useMemo, useState } from "react";

import { AskForm } from "@/components/ask-form";
import { ConversationTurn } from "@/components/conversation-turn";
import { FilingTabs } from "@/components/filing-tabs";
import { FilingViewer } from "@/components/filing-viewer";
import { initialAnswerState, reduceAnswer } from "@/lib/answer";
import type { AnswerState } from "@/lib/answer";
import { askStream } from "@/lib/api";
import type { AskFilters } from "@/lib/api";
import { getOrCreateConversationId, startNewConversation } from "@/lib/conversation";
import { closeTab, initialTabState, openTab } from "@/lib/tabs";
import type { Citation } from "@/lib/types";

type TurnView = { question: string; state: AnswerState };

export default function AskPage() {
  const [turns, setTurns] = useState<TurnView[]>([]);
  const [tabs, setTabs] = useState(initialTabState);
  const [sids, setSids] = useState<Record<string, number[]>>({});

  const streaming = turns.at(-1)?.state.status === "streaming";

  // accession -> tab label, across every turn. The year disambiguates
  // same-company, same-form-type filings that would otherwise render
  // identically-labelled tabs.
  const labels = useMemo(() => {
    const out: Record<string, string> = {};
    for (const turn of turns) {
      for (const citation of turn.state.citations.values()) {
        if (citation.accession) {
          out[citation.accession] =
            `${citation.ticker} ${citation.form_type} ${citation.filing_date.slice(0, 4)}`;
        }
      }
    }
    return out;
  }, [turns]);

  function patchLastTurn(update: (state: AnswerState) => AnswerState) {
    setTurns((previous) => {
      if (previous.length === 0) return previous;
      const next = [...previous];
      const last = next[next.length - 1];
      next[next.length - 1] = { ...last, state: update(last.state) };
      return next;
    });
  }

  async function ask(question: string, filters: AskFilters) {
    const conversationId = getOrCreateConversationId();
    setTurns((previous) => [
      ...previous,
      { question, state: { ...initialAnswerState, status: "streaming" } },
    ]);
    try {
      for await (const event of askStream(question, filters, conversationId)) {
        patchLastTurn((state) => reduceAnswer(state, event));
      }
    } catch (error) {
      patchLastTurn((state) => ({
        ...state,
        status: "error",
        errorMessage:
          error instanceof Error ? error.message : "Could not reach the API.",
      }));
    }
  }

  function newConversation() {
    startNewConversation();
    setTurns([]);
    setTabs(initialTabState);
    setSids({});
  }

  function select(citation: Citation) {
    // Unverified and unattributable citations are inert by design (§6.3):
    // there is nothing trustworthy to scroll to.
    if (!citation.verified || citation.accession === "") return;
    setTabs((previous) => openTab(previous, citation.accession));
    setSids((previous) => ({ ...previous, [citation.accession]: citation.sids }));
  }

  return (
    <main className="grid h-screen grid-cols-[minmax(0,5fr)_minmax(0,7fr)] bg-slate-950 text-slate-200">
      <section className="overflow-y-auto border-r border-slate-800 p-5">
        <div className="mb-4 flex items-baseline justify-between">
          <h1 className="font-mono text-sm font-bold tracking-wide text-slate-100">
            EDGAR ANSWERS
          </h1>
          {turns.length > 0 && (
            <button
              type="button"
              onClick={newConversation}
              className="text-xs text-slate-500 underline hover:text-slate-300"
            >
              New conversation
            </button>
          )}
        </div>
        <AskForm disabled={streaming} onSubmit={ask} />
        {turns.length === 0 ? (
          <p className="text-slate-500">Ask a question about a filing.</p>
        ) : (
          turns.map((turn, index) => (
            <ConversationTurn
              key={index}
              question={turn.question}
              state={turn.state}
              onSelect={select}
            />
          ))
        )}
      </section>

      <section className="flex flex-col overflow-hidden">
        <FilingTabs
          tabs={tabs}
          labels={labels}
          onActivate={(accession) => setTabs((previous) => openTab(previous, accession))}
          onClose={(accession) => setTabs((previous) => closeTab(previous, accession))}
        />
        <div className="min-h-0 flex-1 bg-slate-950">
          <FilingViewer tabs={tabs} sids={sids} />
        </div>
      </section>
    </main>
  );
}
