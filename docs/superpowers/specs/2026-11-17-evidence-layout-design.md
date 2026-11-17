# Evidence layout: the answer beside its proof

**Date:** 2026-11-17 · **Status:** approved by Dylan ("do what you think is best"), layout B from
the four-layout canvas, collapsing to C on phones.

## 1. Why

The current `/ask` is a dark split pane: answer on the left, a tabbed full-filing viewer on the
right. It works, but:

- the proof of a citation is a whole filing scrolled to one row, behind tabs;
- nothing says the corpus is only ten companies, so out-of-coverage questions read as failures;
- the page is one 445-line component wired to that one layout.

The new layout puts each citation's **evidence** next to the answer: the cited table (or the
cited sentence with its neighbours) out of the real filing, highlighted. The whole filing opens
only on request.

## 2. Hard requirements

1. **Easy revert.** The current page survives unchanged as `ClassicAsk`, always served at
   `/classic`. `/ask` renders the new layout unless the build has `NEXT_PUBLIC_LAYOUT=classic`.
   Revert = set that env var in Vercel and redeploy. No code change, no git revert. The classic
   e2e specs keep running against `/classic`, so the fallback can't rot.
2. **Coverage is apparent.** A visitor can't miss that only ten companies are covered (§5).
3. **The tutorial keeps working.** Same script, same reducer (`lib/tour.ts`), same steps. Only
   where the callouts render changes (§6).
4. **Nothing below the frontend changes.** No API, prompt or retrieval change, so the 50-run
   tutorial vetting still holds for the model side.

## 3. Layout

**Desktop (≥ 768 px):** two columns.

- Left: header (name, coverage line, Take the tutorial / New conversation), the thread, the
  composer docked at the bottom.
- Right: **Evidence** for the *selected turn* (the latest by default, or the turn whose citation
  was clicked last). It shows a "n of m quotes verified" pill, then one card per citation in
  marker order. Clicking a chip scrolls its card into view and marks it selected.

**Phone (< 768 px):** one column. Each turn's evidence cards render inline under that turn's
answer. Clicking a chip scrolls to its card.

The breakpoint is read with `matchMedia` after mount, not CSS duplication, so each card (and its
parsed excerpt) exists once.

**Theme:** the current dark theme. The canvas used a light theme only so the four layouts could
be compared on layout alone, and the look was always a separate choice. Staying dark lets the
new page reuse the tutorial cards, answer stream, citation chips and under-the-hood panel as
they are. A light theme can follow as its own change.

## 4. The evidence card

- **Header:** marker chip, `TICKER · FORM`, filing date, and a status: "Exact match in the
  filing" when verified, "Quote not found in the filing" (red) when not.
- **Excerpt** (verified citations only), built by `lib/excerpt.ts` from the filing's parsed
  viewer HTML:
  - a cited sid on a `<tr>` → the whole enclosing `<table>`, so the header rows come along;
  - a cited sentence span → that sentence plus the previous and next sentence by sid, as one
    paragraph.

  The existing `applyHighlight` marks the cited rows, sentences and figure cells. The excerpt box
  caps its height (scrolls inside) and centres the highlight with the existing `centreInPane`.
- **Unverified:** no excerpt, just the model's quote struck through with the red status. This
  keeps design §6.3's "visible badge, never a silent drop".
- **Footer:** "Open full filing" opens the drawer (§4.1).
- Filings are fetched and parsed once per accession (`lib/filing-cache.ts`, a module-level
  promise map), and shared by cards and the drawer.

### 4.1 Full-filing drawer

The existing `FilingViewer` (tab state with one tab, plus that citation's highlight) in an
overlay. On desktop it's a right-hand drawer, 60% wide, over a scrim. On a phone it fills the
screen. It closes with ✕, the scrim, or Escape, and focus returns to the button that opened it.

## 5. Coverage

1. **Header coverage line, always visible:** "Covers only Apple, Amazon, Alphabet, Johnson &
   Johnson, JPMorgan Chase, Meta, Microsoft, NVIDIA, Tesla and Walmart · 10-K and 10-Q filings,
   Aug 2023 – Jul 2026". It's built from `/companies`, reusing `lib/coverage.ts`'s short names
   and range.
2. **Composer:** the ticker buttons get a visible label, "Limit to:", and the placeholder names
   the scope ("Ask about these 10 companies' filings…").
3. **Empty state:** the full coverage sentence (PR #33). No starter questions: every question the demo
   offers must be vetted, and only the tutorial's two are.
4. **Out-of-coverage hint:** an answer that finishes with zero citations gets, under it: "This
   demo only covers the 10 companies above, filings from Aug 2023 to Jul 2026. Questions outside
   that can't be answered from it." It's decided from the citation count, so it never shows
   under a real answer.

## 6. Tutorial placement

| Step | Classic | Evidence layout |
| --- | --- | --- |
| welcome | above the form | same |
| watch / followup | above the under-the-hood panel | same |
| click | below the answer, chip pulsing | same |
| source | top of the viewer | directly above the clicked citation's evidence card (right column on desktop, inline on a phone) |
| more / closing / interrupted | below the turn | same |

On desktop, `showPayoff` at the closing becomes selecting the latest turn, whose cards are
already on screen, rather than opening a viewer tab. The copy is unchanged ("That's the exact
figure in the original filing, highlighted…" is true of a card).

## 7. Tests

- **vitest:** `excerpt.test.ts` (jsdom): a table row gives the whole table; a sentence gives a
  three-sentence window at the start, middle and end; an unknown sid gives null. Plus the
  out-of-coverage predicate and the header coverage line.
- **Playwright** (`e2e/evidence.spec.ts`, mocked API like the other specs):
  - the coverage line is visible;
  - an answer renders one card per citation, with the cited row highlighted;
  - a chip click selects and scrolls to its card;
  - Open full filing opens the drawer, and Escape closes it;
  - phone width puts the cards inline under the answer;
  - an unverified citation shows the red status;
  - a zero-citation answer shows the hint;
  - the scripted tutorial walks welcome → click → source → more → follow-up → closing, with no
    horizontal overflow at 390 px.
- **Classic specs** move to `/classic` unchanged.
- **Live QA** against the local API at 1440×900 and 390×844, then against prod after deploy.

## 8. Round 2 (2026-11-17, after review on a phone)

Dylan's notes on the shipped layout, and what changed:

1. **"It should be obvious when you're in the tutorial."** He chose idea 2 from the design canvas:
   while the tour runs, its panel takes the input bar's place. It shows a "TUTORIAL · Step n of 5"
   label with step dots, and the step's text and buttons. There is no input bar until Done or
   Exit tutorial, and the input coming back in the same spot means "your turn".
   - It is a `variant="dock"` on the shared `TourCallout`, with the same copy and buttons. The
     classic page keeps its inline cards.
   - Steps: 1 watch, 2 click, 3 source, 4 more/follow-up, 5 closing. An interruption has no
     number.
2. **"It says what the corpus is twice."** The header coverage line and the empty-state sentence
   are gone. The coverage is stated once per screen:
   - On the landing: "This app only covers the 10-K and 10-Q SEC filings of 10 companies, from
     the last 3 years.", with the tickers under it.
   - In chat, as a caption under the input: "10-K and 10-Q filings · 10 companies · last 3
     years".
   - Plus the found-nothing hint, which now reuses the same sentence.
3. **"Super simple on a phone; the input bar hugs the bottom."**
   - A first visit lands on a full-screen **Landing**: what it is, the coverage, and two buttons,
     "Take the 1-minute tour" and "Skip, ask my own question". This replaces the welcome card
     above the form.
   - In chat, the input bar is `sticky bottom-0` with safe-area padding, like a chat app's.
   - The ten ticker buttons fold into one "All 10 companies ▾" button that opens the filter
     panel. The new **Composer** owns that.
   - The classic `AskForm` is untouched.
   - "Take the tour" in the header goes back to the landing.
