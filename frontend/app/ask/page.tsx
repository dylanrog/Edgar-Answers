"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { AskForm } from "@/components/ask-form";
import { ConversationTurn } from "@/components/conversation-turn";
import { ExampleQuestions } from "@/components/example-questions";
import { FilingTabs } from "@/components/filing-tabs";
import { FilingViewer } from "@/components/filing-viewer";
import { TourCallout, TourWelcome } from "@/components/tour-coach";
import { initialAnswerState, reduceAnswer } from "@/lib/answer";
import type { AnswerState } from "@/lib/answer";
import { askStream, fetchCompanies } from "@/lib/api";
import type { AskFilters } from "@/lib/api";
import { getOrCreateConversationId, startNewConversation } from "@/lib/conversation";
import { EXAMPLES, askExampleFresh } from "@/lib/examples";
import type { Example } from "@/lib/examples";
import {
  askEvent,
  firstVerifiedMarker,
  hasSeenTour,
  markTourSeen,
  reduceTour,
} from "@/lib/tour";
import type { TourEvent, TourStep } from "@/lib/tour";
import { closeTab, initialTabState, openTab } from "@/lib/tabs";
import type { Highlight } from "@/lib/highlight";
import type { Citation, Company } from "@/lib/types";

const noop = () => {};

// `id` is a stable React key: index keys let a replaced thread reuse the old
// turn's component state (its already-settled panel).
type TurnView = { id: number; question: string; state: AnswerState };

export default function AskPage() {
  const [turns, setTurns] = useState<TurnView[]>([]);
  const [tabs, setTabs] = useState(initialTabState);
  const [highlights, setHighlights] = useState<Record<string, Highlight>>({});
  const [companies, setCompanies] = useState<Company[]>([]);
  // Starts "off" and is read from storage on mount: the page is prerendered,
  // so reading storage during render would mismatch on hydration.
  const nextTurnId = useRef(0);
  const askAbort = useRef<AbortController | null>(null);
  const [tour, setTour] = useState<TourStep>("off");

  useEffect(() => {
    // Deferred so the first client render matches the prerendered "off".
    if (!hasSeenTour()) queueMicrotask(() => setTour(reduceTour("off", { type: "start" })));
  }, []);

  function dispatch(event: TourEvent) {
    const next = reduceTour(tour, event);
    setTour(next);
    // A dismissed or finished tour must never come back by itself.
    if (next === "off" && (event.type === "skip" || event.type === "finish")) {
      markTourSeen();
    }
  }

  useEffect(() => {
    // A failed company list only costs the filter dropdown and friendly
    // names, so it must not block asking questions.
    fetchCompanies()
      .then(setCompanies)
      .catch(() => setCompanies([]));
  }, []);

  const names = useMemo(
    () => Object.fromEntries(companies.map((c) => [c.ticker, c.name])),
    [companies],
  );

  const streaming = turns.at(-1)?.state.status === "streaming";

  const threadRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<HTMLElement>(null);

  // When a new turn is added, pull its question to the top of the thread so
  // the answer streams into view below it. Keyed on the turn count, so it
  // runs once per question rather than on every token.
  useEffect(() => {
    const last = threadRef.current?.querySelector("article:last-of-type");
    last?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [turns.length]);

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
    dispatch(askEvent(tour));
    askAbort.current?.abort();
    const controller = new AbortController();
    askAbort.current = controller;
    const conversationId = getOrCreateConversationId();
    const id = nextTurnId.current++;
    setTurns((previous) => [
      ...previous,
      { id, question, state: { ...initialAnswerState, status: "streaming" } },
    ]);
    try {
      for await (const event of askStream(
        question,
        filters,
        conversationId,
        controller.signal,
      )) {
        if (controller.signal.aborted) return;
        patchLastTurn((state) => reduceAnswer(state, event));
      }
    } catch (error) {
      // An aborted ask belongs to a thread that was cleared: no error state.
      if (controller.signal.aborted) return;
      patchLastTurn((state) => ({
        ...state,
        status: "error",
        errorMessage:
          error instanceof Error ? error.message : "Could not reach the API.",
      }));
    }
  }

  function newConversation() {
    askAbort.current?.abort();
    startNewConversation();
    setTurns([]);
    setTabs(initialTabState);
    setHighlights({});
  }

  function select(citation: Citation) {
    // Unverified and unattributable citations are inert by design (§6.3):
    // there is nothing trustworthy to scroll to.
    if (!citation.verified || citation.accession === "") return;
    dispatch({ type: "citationClicked" });
    setTabs((previous) => openTab(previous, citation.accession));
    setHighlights((previous) => ({
      ...previous,
      [citation.accession]: { sids: citation.sids, cells: citation.cells },
    }));
    // Stacked layout (below md): the viewer is further down the page, so bring
    // it on screen. Its own highlight scroll then positions the sentence.
    if (window.matchMedia("(max-width: 767px)").matches) {
      viewerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function pickExample(example: Example) {
    askExampleFresh(example, { newConversation, ask });
  }

  function takeTour() {
    newConversation();
    dispatch({ type: "start" });
  }

  function retry() {
    const question = turns.at(-1)?.question;
    if (question === undefined) return;
    askExampleFresh({ question }, { newConversation, ask });
  }

  function callout(step: "watch" | "click" | "source" | "closing" | "interrupted") {
    return (
      <TourCallout
        step={step}
        onSkip={() => dispatch({ type: "skip" })}
        onNext={() => dispatch({ type: "next" })}
        onFinish={() => dispatch({ type: "finish" })}
        onRetry={retry}
        examples={EXAMPLES}
        onPick={pickExample}
        disabled={streaming}
      />
    );
  }

  return (
    <main className="flex flex-col bg-slate-950 text-slate-200 md:grid md:h-screen md:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <section className="flex min-w-0 flex-col border-b border-slate-800 md:min-h-0 md:border-b-0 md:border-r">
        <div className="flex items-baseline justify-between px-5 pb-3 pt-5">
          <h1 className="font-mono text-sm font-bold tracking-wide text-slate-100">
            EDGAR ANSWERS
          </h1>
          <div className="flex items-baseline gap-4">
            <button
              type="button"
              onClick={takeTour}
              className="text-xs text-slate-500 underline hover:text-slate-300"
            >
              Take the tour
            </button>
            {turns.length > 0 && (
              <button
                type="button"
                onClick={() => {
                  newConversation();
                  // Mid-tour, an empty thread leaves callouts nothing to anchor to.
                  if (tour !== "off") dispatch({ type: "start" });
                }}
                className="text-xs text-slate-500 underline hover:text-slate-300"
              >
                New conversation
              </button>
            )}
          </div>
        </div>

        {turns.length > 0 && (
          <div ref={threadRef} className="px-5 pb-4 md:min-h-0 md:flex-1 md:overflow-y-auto">
            {turns.map((turn, index) => {
              const latest = index === turns.length - 1;
              const lastState = turn.state;
              return (
              <ConversationTurn
                key={turn.id}
                question={turn.question}
                state={turn.state}
                onSelect={select}
                names={names}
                expanded={index === turns.length - 1}
                onSettled={
                  latest
                    ? () =>
                        dispatch(
                          lastState.status === "error"
                            ? { type: "errored" }
                            : {
                                type: "settled",
                                hasVerified:
                                  firstVerifiedMarker(lastState.citations) !== null,
                              },
                        )
                    : noop
                }
                emphasizeMarker={
                  latest && tour === "click"
                    ? firstVerifiedMarker(turn.state.citations)
                    : null
                }
                coachAbovePanel={latest && tour === "watch" ? callout("watch") : null}
                coachBelowAnswer={latest && tour === "click" ? callout("click") : null}
                coachBelowTurn={
                  latest && (tour === "closing" || tour === "interrupted")
                    ? callout(tour)
                    : null
                }
              />
              );
            })}
          </div>
        )}

        {turns.length === 0 && tour === "welcome" && (
          <TourWelcome
            examples={EXAMPLES}
            onPick={pickExample}
            onSkip={() => dispatch({ type: "skip" })}
            disabled={streaming}
          />
        )}

        <AskForm companies={companies} disabled={streaming} onSubmit={ask} docked={turns.length > 0} />

        {turns.length === 0 && tour === "welcome" && <div className="flex-1" />}

        {turns.length === 0 && tour !== "welcome" && (
          <div className="flex-1 px-5 pt-4">
            <p className="mb-3 text-slate-500">Ask a question about a filing.</p>
            {tour === "off" && (
              <ExampleQuestions
                examples={EXAMPLES}
                onPick={pickExample}
                disabled={streaming}
              />
            )}
          </div>
        )}
      </section>

      <section ref={viewerRef} className={`flex min-w-0 flex-col overflow-hidden md:h-auto ${tabs.open.length > 0 ? "h-[70dvh]" : "h-auto"}`}>
        {tour === "source" && <div className="shrink-0 px-3 pb-2">{callout("source")}</div>}
        <FilingTabs
          tabs={tabs}
          labels={labels}
          onActivate={(accession) => setTabs((previous) => openTab(previous, accession))}
          onClose={(accession) => setTabs((previous) => closeTab(previous, accession))}
        />
        <div className="min-h-0 flex-1 bg-slate-950">
          <FilingViewer tabs={tabs} highlights={highlights} />
        </div>
      </section>
    </main>
  );
}
