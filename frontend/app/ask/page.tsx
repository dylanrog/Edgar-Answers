"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { AskForm } from "@/components/ask-form";
import { ConversationTurn } from "@/components/conversation-turn";
import { FilingTabs } from "@/components/filing-tabs";
import { FilingViewer } from "@/components/filing-viewer";
import { TourCallout, TourWelcome } from "@/components/tour-coach";
import { initialAnswerState, reduceAnswer } from "@/lib/answer";
import type { AnswerState } from "@/lib/answer";
import { askStream, fetchCompanies } from "@/lib/api";
import type { AskFilters } from "@/lib/api";
import { getOrCreateConversationId, startNewConversation } from "@/lib/conversation";
import { TUTORIAL, askFresh } from "@/lib/tutorial";
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
  const [clicked, setClicked] = useState<Citation | null>(null);
  const [tour, setTour] = useState<TourStep>("off");
  // Until the mount-time check has run, "off" only means "not read yet": the
  // header's tutorial link waits for it, or a first-time visitor sees it flash
  // before the welcome card replaces it.
  const [tourChecked, setTourChecked] = useState(false);

  useEffect(() => {
    // Deferred so the first client render matches the prerendered "off".
    queueMicrotask(() => {
      if (!hasSeenTour()) setTour(reduceTour("off", { type: "start" }));
      setTourChecked(true);
    });
  }, []);

  function dispatch(event: TourEvent): TourStep {
    const next = reduceTour(tour, event);
    setTour(next);
    // A dismissed or finished tour must never come back by itself.
    if (next === "off" && (event.type === "skip" || event.type === "finish")) {
      markTourSeen();
    }
    return next;
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

  // Scrolls the page to the viewer after the click has rendered, not in the
  // click handler: until then the viewer is a short placeholder at the foot
  // of the page, so a scroll aimed at it stops at the old page end, short of
  // the now-taller viewer and its source card. (The highlight scrolls only
  // the pane, so nothing else would finish the job.)
  const [revealViewer, setRevealViewer] = useState(0);
  useEffect(() => {
    if (revealViewer > 0) viewerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [revealViewer]);

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

  async function ask(
    question: string,
    filters: AskFilters,
    options?: { tourEvent?: TourEvent },
  ) {
    dispatch(options?.tourEvent ?? askEvent(tour));
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

  /** Opens a citation's filing in the viewer with the citation highlighted. */
  function openCitation(citation: Citation) {
    setClicked(citation);
    setTabs((previous) => openTab(previous, citation.accession));
    setHighlights((previous) => ({
      ...previous,
      [citation.accession]: { sids: citation.sids, cells: citation.cells },
    }));
  }

  /**
   * The closing step shows the follow-up's payoff: on a split screen the
   * viewer opens the latest answer's first verified citation, as if clicked,
   * so the highlighted 2024 figure sits beside the answer. Not on a phone,
   * where the viewer is below the thread and opening it would scroll away from
   * the closing card.
   */
  function showPayoff(state: AnswerState) {
    if (!window.matchMedia("(min-width: 768px)").matches) return;
    const marker = firstVerifiedMarker(state.citations);
    const citation = marker === null ? undefined : state.citations.get(marker);
    if (citation !== undefined) openCitation(citation);
  }

  function select(citation: Citation) {
    // Unverified and unattributable citations are inert by design (§6.3):
    // there is nothing trustworthy to scroll to.
    if (!citation.verified || citation.accession === "") return;
    dispatch({ type: "citationClicked" });
    openCitation(citation);
    // Stacked layout (below md): the viewer is further down the page, so bring
    // it on screen. Its own highlight scroll then positions the sentence.
    if (window.matchMedia("(max-width: 767px)").matches) setRevealViewer((n) => n + 1);
  }

  // The scripted starts (welcome card, Try again) are the only asks that run
  // the tutorial; a typed question leaves it (askEvent).
  function startTutorialQuestion() {
    askFresh(TUTORIAL.question, {
      newConversation,
      ask: (question, filters) => ask(question, filters, { tourEvent: { type: "asked" } }),
    });
  }

  function askFollowUp() {
    // The open filing belongs to turn 1; next to a fiscal-2024 answer it would
    // read as that answer's source. Start the viewer empty until the visitor
    // clicks one of the follow-up's citations.
    setTabs(initialTabState);
    setHighlights({});
    // Same conversation: the follow-up only makes sense with its context.
    ask(TUTORIAL.followUp, {}, { tourEvent: { type: "followUp" } });
  }

  function takeTour() {
    newConversation();
    dispatch({ type: "start" });
  }

  function callout(
    step: "watch" | "click" | "source" | "more" | "followup" | "closing" | "interrupted",
    target?: Citation,
  ) {
    return (
      <TourCallout
        step={step}
        marker={target?.marker}
        onSkip={() => dispatch({ type: "skip" })}
        onNext={() => dispatch({ type: "next" })}
        onFinish={() => dispatch({ type: "finish" })}
        onRetry={startTutorialQuestion}
        onFollowUp={askFollowUp}
        figure={((target ?? clicked)?.cells.length ?? 0) > 0}
        followUpResolved={turns.at(-1)?.state.standaloneQuestion != null}
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
            {/* Hidden while it runs: the welcome card is the one call to action. */}
            {tourChecked && tour === "off" && (
              <button
                type="button"
                onClick={takeTour}
                className="text-xs text-slate-500 underline hover:text-slate-300"
              >
                Take the tutorial
              </button>
            )}
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
              const emphasized =
                latest && tour === "click" ? firstVerifiedMarker(turn.state.citations) : null;
              return (
              <ConversationTurn
                key={turn.id}
                question={turn.question}
                state={turn.state}
                onSelect={select}
                names={names}
                // At the closing the panel folds to its summary line, so the
                // answer it would otherwise push off screen stays in view.
                expanded={latest && tour !== "closing"}
                onSettled={
                  latest
                    ? () => {
                        const before = tour;
                        const next = dispatch(
                          lastState.status === "error"
                            ? { type: "errored" }
                            : {
                                type: "settled",
                                hasVerified:
                                  firstVerifiedMarker(lastState.citations) !== null,
                              },
                        );
                        if (next === "closing" && before !== "closing") showPayoff(lastState);
                      }
                    : noop
                }
                emphasizeMarker={emphasized}
                coachAbovePanel={
                  latest && (tour === "watch" || tour === "followup")
                    ? callout(tour)
                    : null
                }
                coachBelowAnswer={
                  emphasized !== null
                    ? callout("click", turn.state.citations.get(emphasized))
                    : null
                }
                coachBelowTurn={
                  latest && (tour === "more" || tour === "closing" || tour === "interrupted")
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
            onAsk={startTutorialQuestion}
            onSkip={() => dispatch({ type: "skip" })}
            disabled={streaming}
          />
        )}

        <AskForm
          companies={companies}
          disabled={streaming}
          onSubmit={ask}
          docked={turns.length > 0}
          quiet={tour === "welcome"}
        />

        {turns.length === 0 && tour === "welcome" && <div className="flex-1" />}

        {turns.length === 0 && tour !== "welcome" && (
          <div className="flex-1 px-5 pt-4">
            <p className="mb-3 text-slate-500">Ask a question about a filing.</p>
          </div>
        )}
      </section>

      {/* On a phone an open viewer is a full screen tall: it is the last thing
          on the page, so anything shorter could not be scrolled to the top,
          and its source card would sit mid-screen under the composer. */}
      <section ref={viewerRef} className={`flex min-w-0 flex-col overflow-hidden md:h-auto ${tabs.open.length > 0 ? "h-[100dvh]" : "h-auto"}`}>
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
