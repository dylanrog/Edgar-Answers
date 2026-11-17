"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { ConversationTurn } from "@/components/conversation-turn";
import { Composer } from "@/components/evidence/composer";
import { EvidenceList, cardId } from "@/components/evidence/evidence-list";
import { FilingDrawer } from "@/components/evidence/filing-drawer";
import { Landing } from "@/components/evidence/landing";
import { TourCallout } from "@/components/tour-coach";
import { initialAnswerState, reduceAnswer } from "@/lib/answer";
import type { AnswerState } from "@/lib/answer";
import { askStream, fetchCompanies } from "@/lib/api";
import type { AskFilters } from "@/lib/api";
import { getOrCreateConversationId, startNewConversation } from "@/lib/conversation";
import { coverage, coverageSummary, foundNothing } from "@/lib/coverage";
import { TUTORIAL, askFresh } from "@/lib/tutorial";
import {
  askEvent,
  firstVerifiedMarker,
  hasSeenTour,
  markTourSeen,
  reduceTour,
} from "@/lib/tour";
import type { TourEvent, TourStep } from "@/lib/tour";
import type { Citation, Company } from "@/lib/types";

const noop = () => {};
// Tailwind's md, in rem as Tailwind writes it, so JS and CSS agree at any
// default font size.
const WIDE = "(min-width: 48rem)";

// `id` is a stable React key: index keys let a replaced thread reuse the old
// turn's component state (its already-settled panel).
type TurnView = { id: number; question: string; state: AnswerState };

/** Which card is selected: a turn and one of its markers. */
type Selection = { turnId: number; marker: number };

const USER_INPUT = ["wheel", "touchstart", "pointerdown", "keydown"] as const;

/** Keeps `target` in view while the page around it settles: for `ms`, any
 *  size change of the page or a card re-aims the scroll, instantly. The
 *  visitor's first wheel, touch, press or key ends it. Returns a stop function. */
function keepInView(target: Element, block: ScrollLogicalPosition, ms = 1500): () => void {
  if (typeof ResizeObserver === "undefined") return () => {};
  const observer = new ResizeObserver(() => target.scrollIntoView({ block }));
  observer.observe(document.body);
  for (const card of document.querySelectorAll("[id^=evidence-]")) observer.observe(card);
  const stop = () => {
    observer.disconnect();
    clearTimeout(timer);
    for (const type of USER_INPUT) window.removeEventListener(type, stop, true);
  };
  const timer = setTimeout(stop, ms);
  for (const type of USER_INPUT) window.addEventListener(type, stop, { capture: true, passive: true });
  return stop;
}

function subscribeWide(onChange: () => void) {
  const query = window.matchMedia(WIDE);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** Two columns (evidence on the right) or one (evidence inline). Read once
 *  per render from matchMedia, so each card exists in exactly one place. The
 *  prerender assumes one column; the client corrects it before paint. */
function useWide(): boolean {
  return useSyncExternalStore(
    subscribeWide,
    () => window.matchMedia(WIDE).matches,
    () => false,
  );
}

/**
 * The evidence layout (spec 2026-11-17): the answer on the left, each
 * citation's proof -- cut from the real filing and highlighted -- on the
 * right; on a phone the proof sits under the answer. The original layout lives
 * on in `ClassicAsk` (served at /classic) as the revert path.
 */
export function EvidenceAsk() {
  const [turns, setTurns] = useState<TurnView[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const nextTurnId = useRef(0);
  const askAbort = useRef<AbortController | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [scrollRequest, setScrollRequest] = useState(0);
  const [drawer, setDrawer] = useState<{ citation: Citation; opener: HTMLElement | null } | null>(
    null,
  );
  const [tour, setTour] = useState<TourStep>("off");
  // See ClassicAsk: "off" only means "not read yet" until the mount check ran.
  const [tourChecked, setTourChecked] = useState(false);
  const [foldedTurn, setFoldedTurn] = useState<number | null>(null);
  const wide = useWide();

  useEffect(() => {
    queueMicrotask(() => {
      try {
        if (!hasSeenTour()) setTour(reduceTour("off", { type: "start" }));
      } finally {
        setTourChecked(true);
      }
    });
  }, []);

  function dispatch(event: TourEvent): TourStep {
    const next = reduceTour(tour, event);
    setTour(next);
    if (next === "off" && (event.type === "skip" || event.type === "finish")) {
      markTourSeen();
    }
    return next;
  }

  useEffect(() => {
    fetchCompanies()
      .then(setCompanies)
      .catch(() => setCompanies([]));
  }, []);

  const names = useMemo(
    () => Object.fromEntries(companies.map((c) => [c.ticker, c.name])),
    [companies],
  );
  const scope = useMemo(() => coverage(companies), [companies]);

  const streaming = turns.at(-1)?.state.status === "streaming";
  const latestId = turns.at(-1)?.id ?? null;
  // The turn whose evidence the right column shows: the clicked one, else the latest.
  const shownTurnId = selection?.turnId ?? latestId;

  const threadRef = useRef<HTMLDivElement>(null);
  const openedLatest = useRef(false);

  useEffect(() => {
    // Direct children only: on a phone each turn holds evidence cards, which
    // are <article>s too.
    const last = threadRef.current?.querySelector(":scope > article:last-of-type");
    last?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [turns.length]);

  // Bring the selected card on screen after the click has rendered. On a wide
  // screen the evidence column scrolls on its own; on a phone the page does.
  useEffect(() => {
    if (scrollRequest === 0 || selection === null) return;
    const target = document.getElementById(cardId(selection.turnId, selection.marker));
    if (target === null) return;
    const block = wide ? "nearest" : "start";
    target.scrollIntoView({ behavior: "smooth", block });
    // Cards above the target grow as their excerpts load (a turn shown for the
    // first time mounts every card at its loading height), which would push
    // the target away. Re-aim while that settles, until the visitor steers.
    return keepInView(target, block);
    // Only a new request scrolls; a re-render with the same selection must not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollRequest]);

  function patchLastTurn(update: (state: AnswerState) => AnswerState) {
    setTurns((previous) => {
      if (previous.length === 0) return previous;
      const next = [...previous];
      const last = next[next.length - 1];
      next[next.length - 1] = { ...last, state: update(last.state) };
      return next;
    });
  }

  async function ask(question: string, filters: AskFilters, options?: { tourEvent?: TourEvent }) {
    dispatch(options?.tourEvent ?? askEvent(tour));
    openedLatest.current = false;
    setSelection(null);
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
      for await (const event of askStream(question, filters, conversationId, controller.signal)) {
        if (controller.signal.aborted) return;
        patchLastTurn((state) => reduceAnswer(state, event));
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      patchLastTurn((state) => ({
        ...state,
        status: "error",
        errorMessage: error instanceof Error ? error.message : "Could not reach the API.",
      }));
    }
  }

  function newConversation() {
    askAbort.current?.abort();
    startNewConversation();
    setTurns([]);
    setSelection(null);
    setDrawer(null);
  }

  function selectCard(turnId: number, marker: number) {
    setSelection({ turnId, marker });
    setScrollRequest((n) => n + 1);
  }

  function select(citation: Citation, turnId: number) {
    // Unverified and unattributable citations are inert by design (§6.3).
    if (!citation.verified || citation.accession === "") return;
    dispatch({ type: "citationClicked" });
    if (turnId === latestId) openedLatest.current = true;
    selectCard(turnId, citation.marker);
  }

  /** At the closing, on a wide screen, select the follow-up's first verified
   *  card so its highlighted 2024 figure is in view beside the answer. Not on
   *  a phone, where scrolling to it would leave the closing card. */
  function showPayoff(turnId: number, state: AnswerState) {
    if (!wide || openedLatest.current) return;
    const marker = firstVerifiedMarker(state.citations);
    if (marker !== null) selectCard(turnId, marker);
  }

  const openFiling = useCallback((citation: Citation, opener: HTMLElement) => {
    setDrawer({ citation, opener });
  }, []);
  const closeFiling = useCallback(() => setDrawer(null), []);

  function startTutorialQuestion() {
    askFresh(TUTORIAL.question, {
      newConversation,
      ask: (question, filters) => ask(question, filters, { tourEvent: { type: "asked" } }),
    });
  }

  function askFollowUp() {
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
    // The click step points at the latest answer's first verified citation.
    if (step === "click" && target === undefined) {
      const latest = turns.at(-1)?.state.citations;
      const marker = latest === undefined ? null : firstVerifiedMarker(latest);
      target = marker === null ? undefined : latest?.get(marker);
    }
    const selected =
      selection === null
        ? undefined
        : turns.find((t) => t.id === selection.turnId)?.state.citations.get(selection.marker);
    return (
      <TourCallout
        step={step}
        marker={target?.marker}
        onSkip={() => dispatch({ type: "skip" })}
        onNext={() => dispatch({ type: "next" })}
        onFinish={() => dispatch({ type: "finish" })}
        onRetry={startTutorialQuestion}
        onFollowUp={askFollowUp}
        figure={((target ?? selected)?.cells.length ?? 0) > 0}
        followUpResolved={turns.at(-1)?.state.standaloneQuestion != null}
        unverified={[...(turns.at(-1)?.state.citations.values() ?? [])].some((c) => !c.verified)}
        outOfCredits={turns.at(-1)?.state.errorCode === "credits_exhausted"}
        disabled={streaming}
        variant="dock"
      />
    );
  }

  function evidenceFor(turn: TurnView) {
    return (
      <EvidenceList
        turnId={turn.id}
        citations={turn.state.citations}
        selectedMarker={selection?.turnId === turn.id ? selection.marker : null}
        onOpenFiling={openFiling}
        // Keyed per turn: showing another turn mounts fresh cards rather than
        // reusing this turn's card [1] for that turn's [1].
        key={turn.id}
      />
    );
  }

  const summary = coverageSummary(companies);
  const nothingHint = (
    <p className="mt-4 rounded-md border border-amber-800/60 bg-amber-950/30 px-3 py-2 text-sm text-amber-100">
      {/* One string: JSX drops the space between an expression and text that
          follows on the next line, which ran "NVIDIA.Questions" together. */}
      {[
        summary ?? "This app only covers ten companies' SEC filings.",
        scope ? `Those are ${scope.names}.` : "",
        "Questions outside that can't be answered from it.",
      ]
        .filter(Boolean)
        .join(" ")}
    </p>
  );

  const shownTurn = turns.find((t) => t.id === shownTurnId);
  // While the tour runs, its panel takes the input bar's place (design canvas,
  // round 2, idea 2): no input means "follow along"; the input coming back
  // means "your turn".
  const inTour = tour !== "off" && tour !== "welcome";

  // Until the mount-time check has run, nothing: a first-time visitor must not
  // see the chat flash before the landing replaces it.
  if (!tourChecked) return <main aria-busy="true" className="min-h-dvh bg-slate-950" />;

  if (tour === "welcome" && turns.length === 0) {
    return (
      <Landing
        companies={companies}
        onTour={startTutorialQuestion}
        onSkip={() => dispatch({ type: "skip" })}
        disabled={streaming}
      />
    );
  }

  return (
    <main className="flex min-h-dvh flex-col bg-slate-950 text-slate-200 md:grid md:h-dvh md:min-h-0 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section
        inert={drawer !== null}
        className="flex min-w-0 flex-1 flex-col border-slate-800 md:min-h-0 md:border-r"
      >
        <header className="flex items-center gap-4 border-b border-slate-900 px-5 py-3.5">
          <h1 className="font-mono text-sm font-bold tracking-wide text-slate-100">EDGAR ANSWERS</h1>
          <div className="ml-auto flex items-center gap-4">
            {tour === "off" && (
              <button
                type="button"
                onClick={takeTour}
                className="min-h-10 text-sm text-blue-300 hover:text-blue-200"
              >
                Take the tour
              </button>
            )}
            {turns.length > 0 && !inTour && (
              <button
                type="button"
                onClick={newConversation}
                className="min-h-10 text-sm text-blue-300 hover:text-blue-200"
              >
                New chat
              </button>
            )}
          </div>
        </header>

        {turns.length > 0 ? (
          <div ref={threadRef} className="flex-1 px-5 pb-4 pt-4 md:min-h-0 md:overflow-y-auto">
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
                  onSelect={(citation) => select(citation, turn.id)}
                  names={names}
                  expanded={latest && turn.id !== foldedTurn}
                  onSettled={
                    latest
                      ? () => {
                          const before = tour;
                          const next = dispatch(
                            lastState.status === "error"
                              ? { type: "errored" }
                              : {
                                  type: "settled",
                                  hasVerified: firstVerifiedMarker(lastState.citations) !== null,
                                },
                          );
                          if (next === "closing" && before !== "closing") {
                            setFoldedTurn(turn.id);
                            showPayoff(turn.id, lastState);
                          }
                        }
                      : noop
                  }
                  emphasizeMarker={emphasized}
                  sources={foundNothing(turn.state) ? nothingHint : wide ? null : evidenceFor(turn)}
                />
              );
            })}
          </div>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-2.5 px-8 py-12 text-center">
            <p className="text-[22px] font-semibold text-white">What do you want to know?</p>
            <p className="text-sm leading-relaxed text-slate-400">
              For example: “What were Apple&apos;s net sales in fiscal 2025?”
            </p>
          </div>
        )}

        {/* Pinned to the bottom of the screen like a chat app's input. */}
        <div className="sticky bottom-0 z-10 bg-slate-950 px-3 pb-[max(1rem,env(safe-area-inset-bottom))] pt-2 md:px-5">
          {tour !== "off" && tour !== "welcome" ? (
            callout(tour)
          ) : (
            <Composer
              companies={companies}
              disabled={streaming}
              onSubmit={ask}
              placeholder={turns.length > 0 ? "Ask a follow-up…" : "Ask about a filing…"}
            />
          )}
        </div>
      </section>

      {wide && (
        <section
          aria-label="Evidence column"
          inert={drawer !== null}
          className="flex min-h-0 min-w-0 flex-col"
        >
          <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-6 pt-1">
            {shownTurn === undefined ? (
              <p className="pt-4 text-sm text-slate-400">
                Each answer&apos;s evidence appears here: the exact table or sentence it quotes,
                cut from the original filing and highlighted.
              </p>
            ) : shownTurn.state.citations.size > 0 ? (
              <>
                {shownTurn.id !== latestId && (
                  <p className="pt-4 text-xs text-slate-400">
                    Showing evidence for an earlier answer: “{shownTurn.question}”
                  </p>
                )}
                {evidenceFor(shownTurn)}
              </>
            ) : (
              <p className="pt-4 text-sm text-slate-400">
                {shownTurn.state.status === "streaming"
                  ? "Evidence appears as each quote is checked against its filing."
                  : "This answer has no evidence to show."}
              </p>
            )}
          </div>
        </section>
      )}

      {drawer !== null && (
        <FilingDrawer citation={drawer.citation} opener={drawer.opener} onClose={closeFiling} />
      )}
    </main>
  );
}
