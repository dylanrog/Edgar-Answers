# Guided Tour Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A first-time visitor to `/ask` is coached through one vetted example question to a citation click. An "under the hood" panel, driven by real server progress events, shows each stage of the answer as it happens.

**Architecture:** The backend gains one additive SSE event, `progress`, emitted from `answer_stream` after each stage. A new eval command, `python -m evals examples`, vets the example questions. It runs each one 5× through the production path against pinned gold, which is stored in golden-set format in `backend/evals/examples.yaml`. On the frontend, all logic sits in framework-free `lib/` modules:
- the reducer gains progress state;
- `lib/pipeline-steps.ts` derives the panel's lines;
- `lib/tour.ts` is the coach state machine;
- `lib/examples.json` is the shipped example list.

`components/` stay thin renderers. On phones, `/ask` stacks vertically.

**Tech Stack:** FastAPI SSE, psycopg 3, pytest; Next.js App Router, React, Tailwind 4, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-09-guided-tour-design.md`. Read it. The anti-goals in §4 are the review bar.

## Global Constraints

- Python 3.13; `ruff==0.16.1`. `ruff check .` and `pytest -v` stay green on every commit, run from `backend/`.
- Frontend: `npm run lint`, `npm test` and `npm run test:e2e` stay green. Run them from `frontend/` **in PowerShell**, because node is not on the git-bash PATH.
- Commit messages and PR bodies carry **no AI attribution** of any kind.
- The sentence-anchoring invariant, verification logic, retrieval, prompts and chunking are untouched. This work is display plus one additive event.
- `progress` is additive. Existing events, their payloads and their order are unchanged.
- No new npm or pip dependencies.
- Branch `guided-tour` in worktree `C:\Users\dylan\Projects\SEC-RAG-guided-tour`. Integrate `main` by **merge, never rebase** (eval provenance).
- Copy for panel lines is plain English first. Technical detail (sids, "vector + keyword") goes in secondary text.
- Never hide a failure to keep the tour pretty. The unverified badge, the error text and the rate-limit sentence always show.
- Example questions use natural phrasing, with plain periods like "fiscal 2025". No filing names and no printed dates.

## Review Focus

1. **A typed question that yields no verified citation during the tour.** The coach must skip the click step and go to the closing step. It must not point at nothing. Pinned in Task 6 (`settled without a verified citation goes to closing`) and Task 9 (e2e).
2. **An error or rate limit mid-tour.** The coach steps aside to `interrupted`, and the existing error UI stays visible. Pinned in Task 6 and Task 9.
3. **localStorage throws** (private mode, blocked storage). The tour must still render and must not crash. It just shows again next visit. Pinned in Task 6.
4. **An example clicked after a conversation already exists** (step 5 buttons, post-tour chips, a reload with a live `sessionStorage` id). It must go out as a fresh conversation, never a follow-up. Pinned in Task 8 (vitest) and Task 9 (e2e asserts a new `conversation_id`).
5. **The existing e2e specs, with the tour showing on first visit.** They must not regress. Each existing spec seeds the "tour seen" flag (Task 9).

---

### Task 0: Integrate the deploy branch's code

PR #28 (deploy part 1) touches `frontend/lib/api.ts` and `backend/src/api/app.py`. The spec sequences it first.

- [ ] **Step 1:** Run `gh pr view 28 --json state -q .state`. If it's `MERGED`, run `git fetch && git merge origin/main` on `guided-tour`. If it's still open, continue anyway, and do this merge in Task 10 Step 1 before opening the PR. Either way, merge, never rebase.

---

### Task 1: `progress` events from `answer_stream`

**Files:**
- Modify: `backend/src/api/answer.py` (`answer_stream`; `AnswerEvent.name` comment)
- Modify: `backend/src/api/queries.py` (new helper)
- Test: `backend/tests/test_answer.py` (db-marked, uses the existing `seeded_conn`/`collect`)
- Modify: `docs/design.md` §6.4

**Interfaces:**
- Produces, on the SSE wire, `event: progress` with one of three payloads, emitted in this order:
  - `{"stage": "targets", "tickers": [str], "filings": [{"accession","ticker","form_type","filing_date"}]}`. Emitted right after `resolve_targets`. `tickers` follows target order. `filings` lists the filings that period handling narrowed to, from every target whose `accessions` is not `None`, and is `[]` when nothing was narrowed. `filing_date` is an ISO string.
  - `{"stage": "retrieved", "passages": int, "filings": [{"accession","ticker","form_type","filing_date","passages": int}]}`. Emitted right after `retrieve_for_targets`. Filings are listed in order of first appearance in `chunks`.
  - `{"stage": "verifying", "quotes": int}`. Emitted after the generate/retry loop and before any `citation`. `quotes` is `len(citations or [])`.
- Produces: `queries.load_filing_summaries(conn, accessions: list[str]) -> list[dict]`, returning the four keys above in the order the accessions are given. An unknown accession is skipped.

- [ ] **Step 1: Write the failing tests** in `test_answer.py`, all `@pytest.mark.db`:
  - `test_progress_events_arrive_in_stage_order`: run `collect(seeded_conn, response_with(<valid quote>, chunk_id))`, then filter the event names. The three `progress` stages come in order `targets`, `retrieved`, `verifying`. `retrieved` comes before the first `token`. `verifying` comes after the last `token` and before the first `citation`.
  - `test_retrieved_progress_counts_passages_by_filing`: the `retrieved` payload has `passages == 1` and `filings == [{"accession": ACCESSION, "ticker": "TSTE", "form_type": "10-K", "filing_date": "2024-11-01", "passages": 1}]`.
  - `test_targets_progress_lists_tickers_and_no_narrowing_without_a_period_detector`: call `answer_stream(..., tickers=["TSTE"])` directly. Expect `tickers == ["TSTE"]` and `filings == []`.
  - `test_targets_progress_lists_narrowed_filings`: pass a stub period detector whose `detect` returns `[ACCESSION]`, plus `tickers=["TSTE"]`. Expect `filings == [{"accession": ACCESSION, "ticker": "TSTE", "form_type": "10-K", "filing_date": "2024-11-01"}]`. Check `tests/fakes.py` for an existing stub period detector before writing one.
  - `test_verifying_progress_counts_zero_when_the_block_never_parses`: given two unparseable responses, expect `quotes == 0`.
  - Extend `test_streams_tokens_then_a_verified_citation_then_done`: change `names[0] == "token"` to "the first non-`progress` event is `token`". Keep everything else.

- [ ] **Step 2:** From `backend/`, run `pytest tests/test_answer.py -v` with `TEST_DATABASE_URL` set. Expected: the new tests FAIL (no `progress` events). Docker Postgres must be up, or pytest hangs.

- [ ] **Step 3: Implement** the three `yield AnswerEvent("progress", {...})` calls in `answer_stream`, and add `load_filing_summaries` (one `SELECT ... WHERE f.accession = ANY(%s)` joined to `companies`, re-ordered in Python). Update the `AnswerEvent.name` comment to list `progress`.

- [ ] **Step 4:** Run `pytest -v` and `ruff check .`. Expected: all pass. `evals/faithfulness.py` already ignores unknown event names, and `test_evals.py` must stay green to prove it.

- [ ] **Step 5:** In `docs/design.md` §6.4, add the `progress` event to the code block with its three payloads and a one-line note: "additive; emitted as each stage finishes; drives the under-the-hood panel (spec 2026-11-09)".

- [ ] **Step 6: Commit** `feat: stream a progress event as each stage of an answer finishes`

---

### Task 2: Example-question vetting command

The examples are golden-format entries in their own file, so `load_golden`, `verify` and `repin` all apply. A comparison question is a `group` with one entry per company, the same as qc001/qc002.

**Files:**
- Create: `backend/evals/examples.yaml` (start with a header comment and an empty list `[]`; Task 3 fills it)
- Create: `backend/evals/examples.py`
- Modify: `backend/evals/__main__.py` (new `examples` subcommand; `verify` gains `--examples`)
- Create: `frontend/lib/examples.json` (starts `[]`; Task 3 fills it)
- Test: `backend/tests/test_examples.py` (no DB, runs in CI)

**Interfaces:**
- Consumes: `harness.load_golden(path)`, `harness.GoldenQuestion`, `faithfulness._REFUSALS`, `faithfulness.values_present`.
- Produces:
  - `examples.EXAMPLES_PATH` and `examples.FRONTEND_EXAMPLES_PATH` (repo-relative `frontend/lib/examples.json`).
  - `examples.load_examples() -> dict[str, list[GoldenQuestion]]`, keyed by group.
  - `examples.judge_run(entries: list[GoldenQuestion], kind: str, answer: str, citations: list[dict], unverified_answer: bool, errored: bool) -> list[str]`. Returns failure reasons, and `[]` means the run passed.
  - `examples.run_examples(conn, embedder, generator, company_detector, period_detector, query_rewriter, *, runs: int = 5) -> bool`, which prints one line per run and a summary.
  - CLI: `python -m evals examples [--runs N]` exits non-zero if any run fails. It **does not** append to `results.jsonl`, because it is a release check, not a metric.
  - `python -m evals verify --examples` checks `examples.yaml` instead of `golden.yaml`.
- `frontend/lib/examples.json` shape: `[{"id": str, "label": str, "question": str, "kind": "table" | "comparison" | "prose", "featured": bool}]`. `id` equals the yaml `group`.

- [ ] **Step 1: Write the failing tests** in `test_examples.py`. Build `GoldenQuestion`s inline.
  - `judge_run` returns `[]` for an answered run where every citation is verified, a citation hits each entry's accession plus gold sids, and the expected values are present. Use a `prose` kind with one entry.
  - It returns a reason containing `"refus"` when the answer contains `"do not contain"`.
  - It returns a reason when the run has no citations, when any citation has `verified: False`, when `unverified_answer` is true, and when `errored` is true.
  - It returns a reason naming the entry's ticker when a verified citation quotes the **right ticker but the wrong accession**. This is the period-confusion case that motivated the spec.
  - It returns a reason when `expected_values` are missing from the answer.
  - For `kind="table"`, it returns a reason when no citation has non-empty `cells`.
  - For `kind="comparison"`, it returns a reason when the verified citations span fewer than 2 distinct tickers.
  - Drift test `test_frontend_examples_match_examples_yaml`: the set of `(id, question)` in `examples.json` equals the set of `(group, question)` in `load_examples()`. Exactly one entry has `featured: true`, and its `kind` is `"table"` (vacuously true while both lists are empty). Every `table` and `comparison` example's yaml entries carry `expected_values`, and every `comparison` group has ≥ 2 entries with distinct tickers.

- [ ] **Step 2:** Run `pytest tests/test_examples.py -v`. Expected: FAIL (module missing).

- [ ] **Step 3: Implement** `examples.py`. `run_examples` calls `answer_stream` with exactly the production arguments that `api/app.py`'s `/ask` passes when the filters are empty: `tickers=None`, `form_type=None`, the period detector, the query rewriter, and `conversation_id=None`, so there is no conversation rewrite. `kind` comes from the matching `examples.json` entry. Wire the CLI the way `cmd_run` builds the Anthropic detectors and generator.

- [ ] **Step 4:** Run `pytest -v` and `ruff check .`. Expected: pass.

- [ ] **Step 5: Commit** `feat: vet demo example questions against pinned gold with evals examples`

---

### Task 3: Pick, pin and vet the example questions (controller task, real API)

This needs the full local corpus (`DATABASE_URL`) and `ANTHROPIC_API_KEY`. It costs well under $1. The controller runs it, not a subagent, and shows Dylan the result.

- [ ] **Step 1: Draft candidates.** Draft 2–3 per kind: table, comparison, prose. Start from the already-pinned golden entries:
  - the `t00x` table-tail entries for table figures (e.g. t003 MSFT total debt, t006 Meta total liabilities);
  - `qc001`/`qc002` for comparisons;
  - the `q0xx` AAPL entries for prose. Avoid q004 and q009, the known period failures.

  Rephrase them where needed to stand alone without a ticker filter.
- [ ] **Step 2: Pin.** For each candidate, write the yaml entries (accession, section, `gold_sids`, `expected_values` with quoted spellings), and confirm the sids by reading the sentences out of the DB. Run `python -m evals verify --examples`. Expected: `all N entries verified`.
- [ ] **Step 3: Vet.** Run `python -m evals examples --runs 5`. Keep only candidates that pass 5/5. **Never loosen a pin to make a run pass.** If no comparison candidate passes, ship without one and record that in the yaml header comment.
- [ ] **Step 4:** Write the survivors to `examples.json`: 3–4 entries, short `label`s (≤ 40 chars), and exactly one `featured` table question. Remove the failed candidates from the yaml, keeping a one-line note per dropped candidate in the header comment.
- [ ] **Step 5:** Run `pytest tests/test_examples.py -v` (the drift test now has content). Expected: PASS.
- [ ] **Step 6: Commit** `feat: pin and vet the demo's example questions`. Report the per-run output to Dylan.

---

### Task 4: Progress state in the reducer

**Files:**
- Modify: `frontend/lib/types.ts` (event doc comment + new types)
- Modify: `frontend/lib/answer.ts`
- Test: `frontend/lib/__tests__/answer.test.ts`

**Interfaces:**
- Produces in `types.ts`:
  - `FilingRef = { accession: string; ticker: string; form_type: string; filing_date: string }`
  - `Progress = { targets: { tickers: string[]; filings: FilingRef[] } | null; retrieved: { passages: number; filings: Array<FilingRef & { passages: number }> } | null; quotes: number | null }`
- Produces: `AnswerState.progress: Progress`, and `initialAnswerState.progress = { targets: null, retrieved: null, quotes: null }`.

- [ ] **Step 1: Write the failing tests:**
  - A `progress` event with `stage: "targets"` sets `progress.targets` and leaves `status` and `prose` alone.
  - `stage: "retrieved"` sets `progress.retrieved`.
  - `stage: "verifying"` sets `progress.quotes`.
  - An unknown `stage` returns the state unchanged.
  - `reduceAnswer` never mutates its input (follow the file's existing pattern).
- [ ] **Step 2:** Run `npm test -- answer`. Expected: FAIL.
- [ ] **Step 3: Implement** the `progress` case in `reduceAnswer`.
- [ ] **Step 4:** Run `npm test` and `npm run lint`. Expected: pass.
- [ ] **Step 5: Commit** `feat: keep each answer's server progress in the answer state`

---

### Task 5: The "under the hood" panel

**Files:**
- Create: `frontend/lib/pipeline-steps.ts`
- Create: `frontend/components/under-the-hood.tsx`
- Modify: `frontend/components/conversation-turn.tsx`
- Modify: `frontend/app/ask/page.tsx` (lift `fetchCompanies` here, pass names down; pass `expanded` and `onSettled`)
- Modify: `frontend/components/ask-form.tsx` (takes `companies: Company[]` as a prop instead of fetching)
- Test: `frontend/lib/__tests__/pipeline-steps.test.ts`

**Interfaces:**
- Produces in `pipeline-steps.ts`:
  - `type StepStatus = "pending" | "running" | "done" | "failed"`
  - `type PanelLine = { key: string; status: StepStatus; text: string; detail?: string }`
  - `buildPanel(state: AnswerState, names: Record<string, string>, revealed: number): PanelLine[]`
  - `isSettled(state: AnswerState, revealed: number): boolean`. True when `status` is `"done"` or `"error"`, and every citation has been revealed (`revealed >= state.citations.size`).
  - `REVEAL_INTERVAL_MS = 400`
- Produces: `<UnderTheHood state names expanded onSettled />`. It owns the reveal counter, which ticks every `REVEAL_INTERVAL_MS` once `progress.quotes !== null` and stops at `citations.size`. It calls `onSettled()` once when `isSettled` turns true.
- Produces: `ConversationTurn` takes the new props `names`, `expanded: boolean`, `onSettled: () => void` and `emphasizeMarker: number | null` (used by Task 8). `CitationChip` takes `emphasized?: boolean`, which adds a pulsing ring.

Panel lines (`buildPanel`), with copy fixed here:

| key | text | detail | status rule |
| --- | --- | --- | --- |
| `targets` | `Understood the question: {names joined ", "}`, plus ` · {ticker form year, …}` when filings were narrowed. With no tickers: `Searching every company` | `company and period detection` | running until `progress.targets` arrives |
| `search` | `Searched the filings: found {n} passages in {k} filings` | `vector + keyword search · {ticker form year, …}` | running after targets, done when `retrieved` arrives |
| `write` | `Writing the answer` | `Claude Haiku, told to quote its sources word for word` | running while prose streams, done when `quotes` arrives |
| `check` | `Checking each quote against the source` | — | running until all citations are revealed |
| `quote-{marker}` (one per revealed citation, in marker order) | verified: `Quote {m} → {ticker} {form} {year} ✓ found word for word`; unverified: `Quote {m} ✗ not found in the source` | verified: `sentences {first}–{last}` plus ` · table figure` when `cells` is non-empty | done / failed |

When the state is `"error"`, the line that was running becomes `failed`. Year is `filing_date.slice(0, 4)`. Names fall back to the ticker.

- [ ] **Step 1: Write the failing tests** for `buildPanel`:
  - Before any progress, there is one line, `targets` running.
  - After `targets` + `retrieved`, the search text is `"Searched the filings: found 8 passages in 3 filings"`.
  - Names map tickers to company names, and an unmapped ticker falls back to itself.
  - `revealed = 1` with 2 citations shows exactly one `quote-` line.
  - An unverified citation gives a `failed` line with `"not found in the source"`.
  - A citation with `cells` has a detail containing `"table figure"`.
  - Error mid-stream: the running line becomes `failed`.
  - `isSettled` is false at `revealed < citations.size` and true at equal (status `done`).
- [ ] **Step 2:** Run `npm test -- pipeline-steps`. Expected: FAIL.
- [ ] **Step 3: Implement** `pipeline-steps.ts`, then the component.
  - Expanded: every line, with the status glyph (`○ ◐ ✓ ✗`) and the detail as secondary `text-slate-500` text.
  - Collapsed: one summary line, `Under the hood: {n} passages · {v}/{c} quotes verified`. A button toggles it.
  - Desktop placement: inside `ConversationTurn`, between the answer and the `SourcesPanel`.
  - `page.tsx` passes `expanded={index === turns.length - 1}` as the default open state. The user can still toggle any turn.
- [ ] **Step 4:** Run `npm test`, `npm run lint` and `npm run test:e2e`. Expected: pass. The existing e2e stubs send no `progress`, so the panel must render without it (the targets line stays running, and status `done` settles it).
- [ ] **Step 5: Commit** `feat: show each answer's real stages in an under-the-hood panel`

---

### Task 6: Tour state machine

**Files:**
- Create: `frontend/lib/tour.ts`
- Test: `frontend/lib/__tests__/tour.test.ts`

**Interfaces:**
- Produces:
  - `type TourStep = "welcome" | "watch" | "click" | "source" | "closing" | "interrupted" | "off"`
  - `type TourEvent = { type: "start" } | { type: "skip" } | { type: "asked" } | { type: "settled"; hasVerified: boolean } | { type: "errored" } | { type: "citationClicked" } | { type: "next" } | { type: "finish" }`
  - `reduceTour(step: TourStep, event: TourEvent): TourStep`
  - `hasSeenTour(): boolean`, `markTourSeen(): void`. Both use `localStorage` key `"edgar-answers.tour-seen"`, with every access wrapped in try/catch. A throw on read means `false`, and a throw on write is swallowed.
  - `firstVerifiedMarker(citations: Map<number, Citation>): number | null`. The lowest marker that is verified and has a non-empty `accession`.

Transitions (anything not listed leaves the step unchanged):

| from | event | to |
| --- | --- | --- |
| any | `skip` | `off` |
| any | `start` | `welcome` |
| `welcome`, `click`, `source`, `interrupted` | `asked` | `watch` |
| `watch` | `settled`, `hasVerified: true` | `click` |
| `watch` | `settled`, `hasVerified: false` | `closing` |
| `watch` | `errored` | `interrupted` |
| `click` | `citationClicked` | `source` |
| `source` | `next` | `closing` |
| `closing` | `finish` | `off` |
| `off` | anything except `start` | `off` |

- [ ] **Step 1: Write the failing tests:** one per table row, plus:
  - `settled without a verified citation goes to closing`;
  - `hasSeenTour is false and markTourSeen does not throw when localStorage throws` (stub `globalThis.localStorage` with getters that throw);
  - `firstVerifiedMarker skips unverified and unattributable citations`.
- [ ] **Step 2:** Run `npm test -- tour`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `npm test` and `npm run lint`. Expected: pass.
- [ ] **Step 5: Commit** `feat: add the guided tour's state machine`

---

### Task 7: Mobile layout

**Files:**
- Modify: `frontend/app/ask/page.tsx`
- Modify: `frontend/components/filing-viewer.tsx` (only if needed for the scroll target)

**Interfaces:**
- Produces: below Tailwind `md` (768 px), `/ask` stacks vertically. The thread and form come first; the tabs and viewer sit below at roughly `70vh`, and the page scrolls instead of each pane scrolling.
  - Desktop (`md:` and up) keeps today's `grid-cols-[minmax(0,5fr)_minmax(0,7fr)] h-screen` layout byte-for-byte in behaviour.
  - On a verified citation click below `md`, the viewer section scrolls into view (`scrollIntoView({ behavior: "smooth", block: "start" })` on a ref, gated by `matchMedia("(max-width: 767px)")`).
  - The viewer's own scroll-to-highlight still runs.

- [ ] **Step 1:** Implement with responsive classes only. No JS layout logic beyond the click scroll.
- [ ] **Step 2:** Run `npm run lint` and `npm run test:e2e`. Expected: pass at the default desktop viewport.
- [ ] **Step 3: Check by hand.** Use the `run` skill, or `npm run dev` plus a 375×812 viewport in devtools, and confirm: no horizontal scroll, the answer is readable, and a citation click brings the highlighted sentence on screen.
- [ ] **Step 4: Commit** `feat: stack the ask page vertically on phones`

(Task 9 pins the mobile behaviour in e2e.)

---

### Task 8: Coach UI, example questions, page wiring

**Files:**
- Create: `frontend/lib/examples.ts` (typed re-export of `examples.json`, plus `featuredExample()`)
- Create: `frontend/components/tour-coach.tsx` (`TourWelcome`, `TourCallout`)
- Create: `frontend/components/example-questions.tsx`
- Modify: `frontend/app/ask/page.tsx`, `frontend/components/conversation-turn.tsx`, `frontend/components/citation-chip.tsx`
- Test: `frontend/lib/__tests__/examples.test.ts`

**Interfaces:**
- Consumes: Task 6's `reduceTour`, `hasSeenTour`, `markTourSeen` and `firstVerifiedMarker`; Task 5's `onSettled`, `emphasizeMarker` and `emphasized`.
- Produces:
  - `type Example = { id: string; label: string; question: string; kind: "table" | "comparison" | "prose"; featured: boolean }`
  - `EXAMPLES: Example[]`
  - `featuredExample(): Example | undefined`
  - `askExampleFresh(example, deps: { newConversation: () => void; ask: (q: string, f: AskFilters) => void })`. A pure helper that calls `newConversation()` then `ask(example.question, {})`, in that order.
  - `<ExampleQuestions examples onPick disabled />`, where the featured one is visually first and highlighted.

Wiring in `page.tsx`:
- `tour` state starts at `"off"`. In a mount `useEffect`, set it to `hasSeenTour() ? "off" : "welcome"`, which avoids a hydration mismatch.
- `dispatch(event)` sets `reduceTour(tour, event)`. When the result is `"off"` and the event was `skip` or `finish`, it calls `markTourSeen()`.
- `ask()` dispatches `asked`.
- The latest turn's `onSettled` dispatches `settled` with `hasVerified: firstVerifiedMarker(...) !== null`, or `errored` when its status is `"error"`.
- `select()` dispatches `citationClicked`.
- Header: a **Take the tour** text button (next to New conversation, always shown). It calls `newConversation()` and then `dispatch({ type: "start" })`.
- Every example pick goes through `askExampleFresh`.

Coach placement (inline, never an overlay, so nothing is covered on a 375 px screen):

| step | where | copy | actions |
| --- | --- | --- | --- |
| `welcome` | replaces the empty-state text above the form | **Answers from SEC filings, with every quote checked.** Pick a question to see how it works. | the example buttons; `Skip tour` |
| `watch` | callout directly above the latest turn's panel | Watch the panel: every quote the model uses is checked against the filing before you see it. | `Skip tour` |
| `click` | callout directly below the latest turn's answer; that turn's `emphasizeMarker = firstVerifiedMarker(...)` | Click the highlighted citation to see it in the original filing. | `Skip tour` |
| `source` | callout at the top of the viewer pane | That's the exact sentence (or figure) in the original filing. The server confirmed the quote word for word. | `Next`; `Skip tour` |
| `closing` | callout below the latest turn | This demo runs on 120 SEC filings from ten companies, but the pipeline underneath (sentence-anchored ingestion, hybrid search, quote verification) works on any document collection. | the non-featured examples; link `See the code on GitHub` → `https://github.com/dylanrog/Edgar-Answers` (`target="_blank" rel="noreferrer"`); `Done` |
| `interrupted` | callout below the latest turn; the turn's existing error text stays visible | That one didn't go through. You can try again or explore on your own. | `Try again` (re-asks the same question fresh); `Skip tour` |

When `tour === "off"` and there are no turns, the empty state shows `ExampleQuestions` (all of them) below the form, as chips.

- [ ] **Step 1: Write the failing tests** in `examples.test.ts`:
  - `featuredExample()` returns the single featured entry (mock the JSON with `vi.mock`, so the test doesn't depend on Task 3's content);
  - `askExampleFresh` calls `newConversation` before `ask`, with `{}` filters.
- [ ] **Step 2:** Run `npm test -- examples`. Expected: FAIL.
- [ ] **Step 3: Implement** the helpers, the components and the wiring above.
- [ ] **Step 4:** Run `npm test` and `npm run lint`. Expected: pass.
- [ ] **Step 5: Commit** `feat: coach first-time visitors through an example question to a citation`

---

### Task 9: End-to-end specs

**Files:**
- Create: `frontend/e2e/tour.spec.ts`
- Modify: `frontend/e2e/highlight.spec.ts`, `conversation.spec.ts`, `multi-source.spec.ts`

**Interfaces:**
- Consumes: the stub pattern in `highlight.spec.ts` (route the full `http://localhost:8000/...` origin).

- [ ] **Step 1: Seed the existing specs.** Add `await page.addInitScript(() => localStorage.setItem("edgar-answers.tour-seen", "1"))` before navigation in each existing spec. Run `npm run test:e2e`. Expected: all existing specs pass.
- [ ] **Step 2: Write `tour.spec.ts`.**

  The stubbed SSE body contains:
  - `progress` targets;
  - `progress` retrieved;
  - tokens with `[1]`;
  - `progress` verifying `quotes: 1`;
  - one verified `citation` with non-empty `cells`;
  - `done`.

  It records each `/ask` request body. Tests:
  - **Full flow:**
    1. Load the page. The welcome copy is visible.
    2. Click the featured example. The panel shows `Searched the filings: found`, and the quote line shows `found word for word` and then `table figure`.
    3. The click callout appears, and the `[1]` chip carries the emphasized state.
    4. Click it. The viewer has `.cited-figure` or `.cited-sentence`, and the source callout is visible.
    5. Click `Next`. The closing text contains `any document collection`, and the GitHub link has the repo href.
    6. Click `Done` and reload. No welcome copy.
  - **Skip:** Click `Skip tour` on welcome and reload. No coach.
  - **No verified citation:** The stub's only citation has `verified: false`. After `done`, the closing callout appears with no click step, and the `unverified` badge is visible.
  - **Error:** The stub returns `error` mid-stream. The interrupted callout appears, and the error text is visible.
  - **Fresh conversation:** Complete one example, then click a second example from the closing step. The second `/ask` body's `conversation_id` differs from the first.
  - **Mobile:** `test.use({ viewport: { width: 375, height: 812 } })` for one flow. After the citation click, `expect(page.locator(".cited-sentence, .cited-figure").first()).toBeInViewport()`. Also check `document.documentElement.scrollWidth <= 375`.

  `examples.json` content is real after Task 3. Pick the featured button by its role and the `featured` label read from the JSON in the spec, not hard-coded.
- [ ] **Step 3:** Run `npm run test:e2e`. Expected: all pass.
- [ ] **Step 4: Commit** `test: pin the guided tour end to end, including phones`

---

### Task 10: Docs, final verification, PR

**Files:**
- Modify: `docs/design.md` (§2 note: the tour is a Phase 5 demo addition, not a scope expansion; §7 frontend: coach + panel + mobile stack)
- Modify: `docs/deployment.md` §7 checklist (add: "run `python -m evals examples --runs 5` with `DATABASE_URL` pointed at prod, after the restore and `VACUUM ANALYZE`, and before posting the link")
- Modify: `CLAUDE.md` "Current state" (one short paragraph: guided tour, `progress` event, `evals examples` as a release check, where `examples.yaml`/`examples.json` live and that they must stay in sync)

- [ ] **Step 1:** If Task 0 deferred it, merge `origin/main` now. Resolve conflicts, then re-run every check.
- [ ] **Step 2:** Write the doc edits above.
- [ ] **Step 3: Full verification**:
  - `backend/`: `ruff check .`, `pytest -v` with `TEST_DATABASE_URL`, `python -m evals examples --runs 5` against the local corpus (all pass);
  - `frontend/` in PowerShell: `npm run lint`, `npm test`, `npm run test:e2e`;
  - by hand, with the real API and DB: the tour in desktop Chrome, at a 375 px viewport, and in Firefox.
- [ ] **Step 4: Commit** `docs: record the guided tour, its progress event and the examples release check`
- [ ] **Step 5:** Push and open the PR into `main` with `gh pr create`. The body summarizes the change and the 5/5 vetting output, with no AI attribution.
