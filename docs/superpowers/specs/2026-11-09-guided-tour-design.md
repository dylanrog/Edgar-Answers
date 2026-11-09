# Guided tour for the public demo — design

Status: draft for review, 2026-11-09. Lands before the Phase 5 go-live (design §12).

## 1. Intent

Dylan will post the deployed demo on LinkedIn. That means few visitors, each
giving it a minute or two: recruiters, engineers, and a few finance people.
Most won't know what to type into an empty box. The demo has to show the
project off without Dylan in the room.

In person, the moment that makes people lean in is **clicking a citation
and seeing the exact sentence highlighted in the original filing**. The
tour exists to get every visitor to that click quickly, and to show them
honestly how the answer was built on the way there. That includes the
server checking each quote against the source text, which is the
project's core idea (design §6.3).

Success: a visitor who has never heard of RAG follows the coach, clicks a
citation within their first ~30 seconds, sees the highlight, and leaves
understanding three things:
1. Answers come with checked quotes.
2. They can see each quote in the source.
3. The same approach would work on any document collection.

## 2. Goals

1. **A coach on `/ask`** that walks a first-time visitor through one
   example question to a citation click (option B from brainstorming:
   straight into the real app, no separate landing page).
2. **An "under the hood" panel** that shows the real stages of the answer
   as they happen. It is driven by **real progress messages from the
   server**, not a front-end animation.
3. **Curated example questions** that show off three things: a **table
   figure** (the click marks the exact cited number), a **multi-company
   comparison** (sources grouped by filing, tabbed viewer), and plain prose.
   Only questions that verify reliably are allowed (§6).
4. **A closing coach step** saying the demo runs on SEC filings, but the
   underlying pipeline works on any document collection.
5. **Mobile:** the panes stack vertically and the coach flow is the same.

## 3. Non-goals

- A separate landing page or marketing site.
- A second, non-SEC corpus. The "any corpus" point is made in words only.
- Pre-recorded or cached answers. Example questions run live.
- Retrieval, prompt, or chunking changes. Ranking is untouched.
- Analytics, accounts, or tracking who finished the tour.
- Any change to hosting, limits, or the deploy itself. That is the
  `deploy` branch's plan (`docs/superpowers/plans/2026-10-30-deploy.md`).

## 4. Anti-goals (would be a failure even if it works)

- **An example question that refuses, or shows an "unverified" badge, in
  front of a LinkedIn visitor.** This is why §6 exists.
- **A panel that fakes it**: steps ticking on a timer rather than on what
  the server actually did. Pacing the *display* of real events is fine
  (§5.3). Inventing events is not.
- **A coach that traps people.** The visitor can always skip the tour and
  just use the app, and a dismissed tour never comes back by itself.
- **Jargon walls.** Each panel line is plain English first. Technical
  detail (sids, chunk counts, "vector + keyword") is secondary text.
- **Hiding failures.** If a live answer does fail during the tour (an
  outage, a rate limit, a rare misquote), the existing honest UI shows it.
  The unverified badge, the rate-limit sentence, and the error text are
  never suppressed to keep the tour pretty.

## 5. How it works

### 5.1 Server: progress events (API↔frontend contract, design §6.4)

`answer_stream` already runs the stages in order:
1. Resolve a follow-up question.
2. Resolve targets (companies, plus filings when period handling engages).
3. Retrieve.
4. Generate, with one retry if the citation block fails to parse.
5. Verify each citation.

Today the browser learns about retrieval only from `done`. Add **one new
SSE event type, `progress`**, emitted as each stage finishes. It is
additive: the existing events and their order are unchanged. The
frontend reducer and `evals/faithfulness.py` already ignore unknown event
names, so older clients keep working.

Required payloads. Field names are the builder's to finalize. Record them
in design §6.4 and `frontend/lib/types.ts`.

| `stage` | emitted | carries |
| --- | --- | --- |
| `targets` | after `resolve_targets` | the tickers detected or passed in, and, when period handling narrowed the search, the filings it narrowed to (ticker, form type, filing date) |
| `retrieved` | after `retrieve_for_targets` | number of passages, plus the distinct filings they came from (ticker, form type, filing date, count) |
| `verifying` | after the citation block parses, before verification | number of quotes to check |

Generation needs no progress event, because `token` events are the live
signal. Per-quote results need no new event either: the existing
`citation` events already carry `verified`, `ticker`, `form_type`,
`filing_date`, `sids`, `quote` and `cells`. The panel reads them.

The `resolved` event (rewritten follow-up) already exists. The panel may
show it as a step when present.

### 5.2 Frontend state

Extend `AnswerState` (`lib/answer.ts`, still a pure reducer, still
vitest-tested) with the progress information. All tour logic goes in a
framework-free `lib/` module with unit tests, matching the existing
pattern (`lib/` holds the logic, `components/` are thin renderers):
current step, the transitions on answer events and citation clicks,
skip, and restart.

### 5.3 The "under the hood" panel

Lines, in order, each ticking from pending → running → done:

1. **Understood the question**: "Apple, Microsoft". When period handling
   narrowed the search, the filings it narrowed to follow, e.g. "· AAPL
   10-K 2025, MSFT 10-K 2025". This uses company names from `/companies`
   where available. *My call:* no derived "fiscal 2025" label. Nothing on
   the server produces one (`PeriodDetector` returns accessions), and
   deriving it from filing dates needs per-company fiscal-year logic. A
   wrong guess would put an invented period on screen.
2. **Searched the filings**: "Found 8 passages in 3 filings". The
   secondary text lists the filings and says "vector + keyword search".
3. **Writing the answer**: running while tokens stream.
4. **Checking each quote against the source**: one sub-line per citation,
   revealed one at a time at a readable pace (*builder's call*, roughly
   300–500 ms apart). For example: "Quote 1 → AAPL 10-K 2025 ✓ found word
   for word". The secondary text gives sentence ids, and says "table row"
   when `cells` is non-empty. An unverified quote shows as "✗ not found
   in the source", matching the existing badge.

The pacing applies only to the panel's reveal. Citation chips and the
answer render exactly as today.

Placement: on desktop, the panel sits with each conversation turn in the
left pane. The latest turn's panel is expanded and earlier turns' panels
collapse to one summary line (*my call*). After the tour the panel stays
available on every answer, collapsible, because it's interesting on its
own (*my guess, confirmed in brainstorming*).

Table cells: the panel says "table row" / "figure". It does not need row
or column labels. Adding labels to the `citation` event is out of scope
unless it's trivial (*builder's call*).

### 5.4 The coach

The coach is a small card anchored near what it's talking about. It is
not a full-screen modal; the page stays usable underneath. Steps:

1. **Welcome** (empty `/ask`): one line on what this is, the example
   questions as buttons, and **Skip tour**. The table-figure question is
   the highlighted first pick (*my call*: simplest and most impressive
   click). The visitor may pick any example.
2. **Watch it work** (while streaming): points at the panel. For example:
   "Every quote the model uses is checked against the filing before you
   see it."
3. **Click a citation** (after `done`, once the panel's reveal has
   finished): points at the first *verified* citation chip and pulses it.
   It waits for the click.
4. **This is the source** (after the click): the viewer has scrolled and
   highlighted. For example: "That's the exact sentence (or figure) in the
   original filing. The server confirmed the quote word for word."
   **Next**.
5. **Not just SEC filings + handoff**: "This demo runs on 120 SEC filings
   from ten companies, but the pipeline underneath (sentence-anchored
   ingestion, hybrid search, quote verification) works on any document
   collection." Then: the other example questions, the input box, and a
   link to the GitHub repo (`github.com/dylanrog/Edgar-Answers`, public;
   *my call*). **Done**.

Behaviour:
- It shows on first visit. Finishing or skipping sets a
  `localStorage` flag, wrapped in try/catch. If storage is unavailable,
  the tour simply shows again. A **Take the tour** link in the header
  restarts it (*my call*).
- If the visitor ignores the coach and types their own question at step
  1, the tour carries on with that answer (*my call*). If that answer has
  no verified citation, step 3 is skipped and the coach goes to step 5
  without pretending.
- If the answer errors or hits the rate limit, the coach steps aside and
  the existing error UI shows. The coach offers to retry or to skip.
- After the tour, the example questions remain on the empty-state screen
  as chips (*my call*).
- **Clicking an example question always starts a fresh conversation**
  (*my call*). That transition calls `startNewConversation()` and clears
  the thread, tabs and highlights before asking. The reason: with
  history, `answer_stream` runs the conversation rewriter first, which
  can turn the example into a different, unvetted request. §6 vets
  examples with no history, so this keeps that assumption true. It
  covers the step 5 example buttons, the post-tour chips, and **Take the
  tour**. The conversation id lives in `sessionStorage`, so a reloaded,
  empty-looking page could otherwise send the example as a follow-up.
  Typed questions keep today's behaviour.

Exact copy is the builder's to draft and Dylan's to review. Keep each
step to one or two short sentences.

### 5.5 Mobile

Below a breakpoint (*builder's call*, e.g. Tailwind `md`), `/ask` stacks
vertically: thread and panel on top, viewer below. On a citation click,
the viewer scrolls into view so the highlight is visible without the
visitor hunting for it. The coach steps are the same. Coach cards must
fit a ~375 px wide screen without covering what they point at.

## 6. Choosing example questions: reliability bar

Here, "reliable" means the example both verifies **and** gives the right answer from the right filing.

Three or four example questions: at least one table figure, one
multi-company comparison, and one plain prose answer. Phrasing follows
the golden-set convention: natural questions, plain periods like "fiscal
2025", no filing names or printed dates.

**Bar (*my call*):** each question is run **5 times** through the real
`answer_stream` against the full local corpus, and must pass on all 5:
- it answered (not a refusal);
- it has at least one citation, and every citation is verified;
- it has no `unverified_answer` notice;
- the table question cites at least one cell (non-empty `cells`);
- the comparison question cites at least two different tickers;
- **it is the right answer from the right filing** (*Dylan's decision*).
  Quote verification alone proves "quoted accurately", not "correct".
  Period confusion is the system's known weakness (q004/q009): AAPL files
  the same tables every quarter, so a fiscal-2025 question can quote the
  fiscal-2024 filing word for word and pass every check above. Each
  example in the shared list therefore pins its gold the way the golden
  set does:
  - the expected filing(s), by accession;
  - the gold sid(s) a citation must land on;
  - for figure questions, the expected value(s), in the spirit of
    `value_accuracy`.

  A run passes only if its citations hit the pinned filings and sids and
  the answer states the pinned figures. Finding and pinning the gold for
  each candidate is part of the work. Reuse the golden set's existing
  machinery (`evals verify` to confirm pinned sids exist; `repin` if the
  corpus is ever rebuilt) rather than inventing a parallel format.

Build this as a small repeatable script, alongside the evals, that reads
the same example list the frontend uses, so the shipped list and the
vetted list can't drift. Re-run it after any prompt or retrieval change,
and once against the **prod** database at go-live. Prod can choose a
different query plan (the CLAUDE.md retrieval-measurement gotcha).
Whether it appends to `evals/results.jsonl` is the builder's call. It is
a release check, not a quality metric.

Risk to flag: the golden set has never measured multi-company answers
(CLAUDE.md). The comparison question is the likeliest to fail the bar.
If no comparison candidate passes, ship without one and say so. Don't
lower the bar.

## 7. Constraints

- The sentence-anchoring invariant and verification logic are untouched.
  This is display and an additive event.
- No AI attribution in commits or PR bodies. ruff pinned, `ruff check .`
  and `pytest -v` green; `npm run lint`, `npm test`, `npm run test:e2e`
  green.
- Tests: reducer and tour-state unit tests (vitest); backend test that
  `progress` events are emitted in order with the right counts, using the
  existing fake generator; a Playwright spec of the full tour flow on a
  stubbed SSE stream (the existing e2e pattern), including the mobile
  viewport and the skip path.
- Docs: design §6.4 (new event), §7 (frontend: coach + panel), and a §2
  note that the tour is a Phase 5 demo addition, not a scope expansion.
- Work in its own worktree/branch (`guided-tour`). Sequencing (*my call*):
  the `deploy` branch's Part 1 code PR merges first, then this branches
  from `main` and merges before the deploy plan's Part 2 ops. Vercel
  builds production from `main`, so the tour is in the first public
  build. The deploy plan's "no new features" line refers to that plan's
  own scope. This spec is the deliberate exception.

## 8. Left to the builder

Exact progress-event field names; component structure; coach anchoring
and positioning technique (no new dependency unless clearly warranted);
pacing timings; breakpoint; copy drafts; the vetting script's name and
location; how the example list is shared between frontend and script
(one JSON file is the obvious choice).
