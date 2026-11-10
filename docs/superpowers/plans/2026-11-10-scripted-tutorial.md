# Scripted Tutorial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the guided tour's example menu with one scripted, vetted conversation: the tutorial question, an optional "one more example" follow-up asked in the same conversation, and "Skip tutorial" back to the regular site.

**Architecture:** The backend vetting learns two-turn chains. An entry in `examples.yaml` can say it runs `after` another group in the same conversation, and a run passes only if every turn passes. The frontend replaces the example list with a two-question script, `lib/tutorial.json`, and extends the pure `lib/tour.ts` state machine with a `more` step and a `followup` step. `components/` stay thin.

**Tech Stack:** Python 3.13, pytest; Next.js, React, Tailwind 4, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-09-guided-tour-design.md`. **§9 (the 2026-11-10 amendment) overrides earlier sections where they conflict.**

## Global Constraints

- Everything in the 2026-11-09 plan's Global Constraints still binds. In short:
  - ruff 0.16.1 / pytest / npm lint / vitest / e2e stay green on every commit;
  - no AI attribution in commits;
  - no new dependencies;
  - retrieval, prompts, verification and chunking are untouched;
  - failures are never hidden;
  - work in `C:\Users\dylan\Projects\SEC-RAG-guided-tour` on branch `guided-tour`;
  - merge, never rebase.
- Visitor-facing copy says "tutorial", never "tour". Internal identifiers (`tour.ts`, `TourStep`, the `edgar-answers.tour-seen` key) keep their names.
- The tutorial's two questions come only from `frontend/lib/tutorial.json`, and the backend vetting reads the same questions from `backend/evals/examples.yaml`. A CI test fails if they differ.
- The follow-up is asked in the **same** conversation as the first question. Every other way of starting the tutorial question starts a **fresh** conversation: Welcome, Try again and Take the tutorial.

## Review Focus

1. **The follow-up must carry the first turn's `conversation_id`.** If it doesn't, "What about 24?" is unanswerable. Pinned in Task 2 (vitest) and Task 4 (e2e asserts equal ids).
2. **Skip from any step lands on the regular site:** empty-state text only, no example buttons and no callouts. Pinned in Task 4.
3. **Asking your own question at the `more` step leaves the script.** It finishes the tutorial instead of hijacking the visitor's question into the follow-up flow. Pinned in Task 2.
4. **An error during the follow-up** shows the interrupted card with the real error text, and **Try again** restarts the tutorial question in a fresh conversation. Pinned in Tasks 2 and 4.
5. **Phones:** the `more` and `closing` cards scroll into view after "Next" and after the follow-up settles. Pinned in Task 4.

---

### Task 1: Two-turn vetting

**Files:** `backend/evals/harness.py` (`GoldenQuestion` + `load_golden`), `backend/evals/examples.py`, `backend/evals/__main__.py`, `backend/evals/examples.yaml`, `backend/tests/test_examples.py`, new `frontend/lib/tutorial.json`; delete `frontend/lib/examples.json` only in Task 2 (Task 1's drift test switches to `tutorial.json`).

**Interfaces:**
- `GoldenQuestion` gains `after: str = ""` and `requires_cell: bool = False`. `load_golden` reads both, defaulting to `""` and `False`. It raises `ValueError` when `after` names a group that is not defined earlier in the file, or names the entry's own group.
- `examples.judge_run(entries, answer, citations, unverified_answer, errored) -> list[str]`. The `kind` parameter is removed:
  - the comparison check (≥2 distinct verified tickers) applies when the group's entries span ≥2 tickers;
  - the cell check applies per entry with `requires_cell`: some verified citation on that entry's accession must have non-empty `cells`.
  - Everything else is unchanged.
- `examples.chains() -> list[list[str]]` returns ordered group chains, a root followed by the groups that run `after` it, in file order.
- `examples.run_examples(conn, embedder, generator, company_detector, period_detector, query_rewriter, conversation_rewriter, *, runs: int = 10) -> bool`. Each run of a chain:
  - uses one fresh `str(uuid4())` conversation id for every turn in the chain;
  - calls `answer_stream` with exactly the arguments `/ask` uses with empty filters, plus `conversation_id` and `conversation_rewriter`;
  - calls `conn.commit()` after each turn;
  - judges every turn.

  A run passes only if every turn passes. It prints `PASS/FAIL <chain> run N`, with failing turns' reasons, and then `<chain>: k/N`. It does not write `results.jsonl`.
- CLI: `python -m evals examples [--runs N]`, with the default changed to **10**. It builds `AnthropicConversationRewriter`.
- `frontend/lib/tutorial.json` is `{"question": str, "followUp": str}`.
- `examples.yaml` holds exactly one chain:
  - group `tutorial-1`, with entries NVDA and MSFT;
  - group `tutorial-2` with `after: tutorial-1`, with entries NVDA and MSFT.

  Pins, figures, accessions and sids are verbatim from spec §9's gold table. NVDA entries carry `requires_cell: true`. `expected_values` spellings:
  - turn 1: NVDA `[["115,186", "115.2 billion", "115.186 billion"]]`, MSFT `[["168.9 billion"]]`;
  - turn 2: NVDA `[["47,525", "47.5 billion", "47.525 billion"]]`, MSFT `[["137.4 billion"]]`.

  Questions: the turn 1 text is given in the dispatch (spec §9; finalized by probing), and turn 2 is `What about 24?`. The yaml header records the retired examples, the Intelligent Cloud finding, and the vetting result line (the controller fills that in).

- [ ] **Step 1: Write failing tests:**
  - `load_golden` `after` validation, covering both the unknown-group and self-reference cases.
  - `judge_run`:
    - `requires_cell` passes with a cell on the right accession;
    - it fails with no cell;
    - it fails when the cell is only on another accession;
    - the comparison check triggers only for multi-ticker groups.
  - `chains()` ordering.
  - `run_examples` with `answer_stream` monkeypatched to a recording fake. It must assert:
    - both turns of a run share one conversation id;
    - the two runs use different ids;
    - turn 2 is called with the yaml's `What about 24?`;
    - a failing turn 2 fails the run and the function returns False.
  - The drift test: `tutorial.json` `question` equals `tutorial-1`'s question, `followUp` equals `tutorial-2`'s, `tutorial-2.after == "tutorial-1"`, and the yaml holds exactly that one chain.
- [ ] **Step 2: Run them** with `PYTHONPATH="src;." python -m pytest tests/test_examples.py -v` and confirm they fail.
- [ ] **Step 3: Implement.** Keep the existing `_REFUSALS`, `values_present` and production-argument comment. `verify --examples` keeps working.
- [ ] **Step 4: Run the full suite:** `pytest -v` and `ruff check .`, both green. The golden-set tests must be unaffected by the new optional fields.
- [ ] **Step 5: Commit** `feat: vet the tutorial as one two-turn conversation`

### Task 2: Tutorial script and state machine (pure)

**Files:** new `frontend/lib/tutorial.ts` and `frontend/lib/__tests__/tutorial.test.ts`; modify `frontend/lib/tour.ts` and `frontend/lib/__tests__/tour.test.ts`; delete `frontend/lib/examples.ts`, `frontend/lib/examples.json` and `frontend/lib/__tests__/examples.test.ts`.

**Interfaces:**
- `tutorial.ts`:
  - `TUTORIAL: { question: string; followUp: string }`, read from `tutorial.json`;
  - `askFresh(question: string, deps: { newConversation: () => void; ask: (q: string, f: AskFilters) => void }): void`, which calls `newConversation()` and then `ask(question, {})`.
- `tour.ts`:
  - `TourStep = "welcome" | "watch" | "click" | "source" | "more" | "followup" | "closing" | "interrupted" | "off"`;
  - `TourEvent` adds `{ type: "followUp" }`;
  - `askEvent(step)` returns `{type:"finish"}` for `more` and `closing`, and `{type:"asked"}` otherwise.

  Transitions (anything unlisted is unchanged):

  | from | event | to |
  | --- | --- | --- |
  | any | skip | off |
  | any | start | welcome |
  | off | anything but start | off |
  | welcome, click, source, interrupted | asked | watch |
  | watch | settled(hasVerified: true) | click |
  | watch | settled(hasVerified: false) | closing |
  | watch, followup | errored | interrupted |
  | watch, click | citationClicked | source |
  | source | next | more |
  | more | followUp | followup |
  | followup | settled(any) | closing |
  | more, closing | finish | off |

- [ ] **Step 1: Write failing tests.** One per table row, plus:
  - `askEvent` for every step;
  - `followup` ignores `citationClicked` and `next`;
  - `askFresh` calls `newConversation` before `ask`, with `{}`.

  Keep the existing storage and `firstVerifiedMarker` tests.
- [ ] **Step 2:** Run `npm test -- tour tutorial` and confirm the new tests fail.
- [ ] **Step 3: Implement.** Delete the examples module and its test. Every importer is rewired in Task 3, so this commit may leave `page.tsx`/`tour-coach.tsx` importing deleted names. To keep the build green, do Task 2 and Task 3 as consecutive commits with the build checked at Task 3. Alternatively, keep `examples.ts` until Task 3 deletes it. Pick the latter.
- [ ] **Step 4:** Run `npm test` and `npm run lint`, both green.
- [ ] **Step 5: Commit** `feat: script the tutorial as one question and a follow-up`

### Task 3: Coach UI and page wiring

**Files:** `frontend/components/tour-coach.tsx`, `frontend/app/ask/page.tsx`; delete `frontend/components/example-questions.tsx` and `frontend/lib/examples.ts`/`examples.json` (plus its test if it still exists).

**Wiring:**
- **Welcome** (empty thread, `tour === "welcome"`): the copy, the tutorial question as one button, and `Skip tutorial`.
  - Copy: **Answers from SEC filings, with every quote checked.** Here's a question to show you how it works:
  - The button shows the question text. Clicking it calls `askFresh(TUTORIAL.question, …)`.
- **watch:** same as today (above the latest panel).
- **click:** same as today.
- **source:** same as today (top of the viewer). Next dispatches `next`.
- **more:** a callout below the latest turn that scrolls into view. Copy: Want to see one more? Ask a follow-up. The conversation keeps its context, so a short question is enough. Actions:
  - a primary button `Ask "What about 24?"` (the text comes from `TUTORIAL.followUp`). It dispatches `{type:"followUp"}` and then asks `TUTORIAL.followUp` **in the current conversation**, so there is no `newConversation()`, and it must NOT dispatch `askEvent`;
  - `Skip tutorial`.
- **followup:** a callout above the latest turn's panel. Copy: It read "What about 24?" in context. See "Searched for" above. Every figure is checked against the 2024 filings. Action: `Skip tutorial`.
- **closing:** the copy, GitHub link and `Done` stay as today, with **no example buttons**. It scrolls into view.
- **interrupted:** the copy and the visible error stay. `Try again` calls `askFresh(TUTORIAL.question, …)`. `Skip tutorial` is also offered.
- **Every visitor-facing "Skip tour"** becomes `Skip tutorial`. The header link `Take the tour` becomes `Take the tutorial`, with unchanged behaviour.
- **Regular site** (`tour === "off"`, empty thread): only the existing "Ask a question about a filing." text. No examples.
- **`ask()`:** an optional third parameter `{ tourEvent?: TourEvent }` overrides the default `askEvent(tour)` dispatch. The follow-up button passes `{type:"followUp"}`. Everything else is unchanged, including the abort controller and the stable turn ids.
- **Delete** `ExampleQuestions` and every `EXAMPLES`/`pickExample` use.

- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Run `npm test`, `npm run lint` and `NEXT_PUBLIC_API_URL=http://localhost:8000 npm run test:e2e`. The old `tour.spec.ts` will fail on the removed example menu, which is expected. Update only the selectors it needs to keep the *existing three* non-tour specs green. Task 4 rewrites `tour.spec.ts`, so mark that spec's failing tests `test.fixme` in this commit and say so in the report.
- [ ] **Step 3: Commit** `feat: walk visitors through one question, then offer a follow-up or skip`

### Task 4: End-to-end specs

**Files:** `frontend/e2e/tour.spec.ts`, rewritten for the new flow. Use the existing stub pattern and `reloadHydrated` helper, and read question text from `lib/tutorial.json`. The stubs give:
- turn 1: a `progress` sequence, a verified NVDA citation with `cells`, and a verified MSFT prose citation;
- turn 2: a `resolved` event naming fiscal 2024, then progress and citations.

Specs:
1. **Full flow:**
   - welcome shows exactly one question button and `Skip tutorial`;
   - click it, and the panel ticks;
   - the click callout and pulsing `[1]`, then click;
   - `.cited-figure` is visible and the source callout shows;
   - Next brings the more callout with the follow-up button;
   - click it, and the second `/ask` body has the **same** `conversation_id` as the first, with `question` "What about 24?";
   - the followup callout and "Searched for" are visible;
   - the closing callout has the GitHub link and no example buttons;
   - Done, then `reloadHydrated`: no welcome and no callouts.
2. **Skip from welcome:** the regular site shows "Ask a question about a filing.", with zero buttons whose name contains the tutorial question, and no callouts. After `reloadHydrated`, it stays that way.
3. **Skip from `more`:** the same regular-site assertions, with the thread kept.
4. **Typed question at `more`:** it finishes the tutorial (no callouts) and the answer shows.
5. **No verified citation on turn 1:** closing, with no click step and the unverified badge visible.
6. **Error on the follow-up:** the interrupted card plus the error text. Try again sends a **new** `conversation_id` with the tutorial question.
7. **Mid-stream Take the tutorial:** the stale stream never lands (keep the existing approach).
8. **Take the tutorial from an existing thread** restarts at welcome.
9. **Phone, 375×812, through the full flow:** the highlight is in the viewport after the click, and the more and closing cards are `toBeInViewport()` when they appear. `scrollWidth <= 375`.

- [ ] **Step 1:** Write the specs, with no `fixme` left.
- [ ] **Step 2:** Run the full e2e, `npm test` and `npm run lint`, all green.
- [ ] **Step 3: Commit** `test: pin the scripted tutorial end to end`

### Task 5: Docs

`docs/design.md`:
- §7: replace the example-menu description with the scripted tutorial and "Skip tutorial".
- §8: the examples check is a two-turn chain with a default of 10 runs.

`docs/deployment.md`: the go-live line uses `--runs 10`.

`README.md`: if its Demo section describes the tour, align it.

Commit: `docs: describe the scripted tutorial and its two-turn vetting`.
