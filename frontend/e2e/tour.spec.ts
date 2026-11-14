import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import tutorial from "../lib/tutorial.json";

const QUESTION = tutorial.question;
const FOLLOW_UP = tutorial.followUp;
const NVDA = "0001045810-25-000023";
const MSFT = "0000950170-25-100235";
const API = "http://localhost:8000";
const STANDALONE = "What was NVIDIA's Data Center revenue in fiscal 2024?";

const NVDA_HTML = `
  <table><tbody>
    <tr data-sid="30"><td>Year Ended</td></tr>
    <tr data-sid="31"><td>Data Center</td><td>$</td><td>115,186</td><td>$</td><td>47,525</td></tr>
  </tbody></table>`;
const MSFT_HTML = `<p><span data-sid="50">Microsoft Cloud revenue was $168.9 billion.</span></p>`;

const sse = (event: string, data: unknown) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

const nvdaRef = { accession: NVDA, ticker: "NVDA", form_type: "10-K", filing_date: "2025-02-26" };
const msftRef = { accession: MSFT, ticker: "MSFT", form_type: "10-K", filing_date: "2025-07-30" };

const progress = (...refs: { accession: string; ticker: string; form_type: string; filing_date: string }[]) =>
  sse("progress", { stage: "targets", tickers: refs.map((r) => r.ticker), filings: [] }) +
  sse("progress", {
    stage: "retrieved",
    passages: 8,
    filings: refs.map((r) => ({ ...r, passages: 4 })),
  });

const nvdaCitation = (marker: number) =>
  sse("citation", {
    marker,
    verified: true,
    ...nvdaRef,
    sids: [31],
    quote: "Data Center $ 115,186",
    cells: [{ sid: 31, cell: 2 }],
  });
const msftCitation = (marker: number) =>
  sse("citation", {
    marker,
    verified: true,
    ...msftRef,
    sids: [50],
    quote: "Microsoft Cloud revenue was $168.9 billion.",
  });
const unverifiedCitation = sse("citation", {
  marker: 1,
  verified: false,
  accession: "",
  ticker: "",
  form_type: "",
  filing_date: "",
  sids: [],
  quote: "fabricated",
});
const done = (total: number, verified: number) =>
  sse("done", {
    chunks_retrieved: 8,
    citations_total: total,
    citations_verified: verified,
    unverified_answer: false,
  });

const TURN1 =
  progress(nvdaRef, msftRef) +
  sse("token", { text: "NVIDIA Data Center revenue was $115,186 million [1]; Microsoft Cloud was $168.9 billion [2]." }) +
  sse("progress", { stage: "verifying", quotes: 2 }) +
  nvdaCitation(1) +
  msftCitation(2) +
  done(2, 2);

// A long answer, so the phone test runs against a thread taller than the
// screen, as a real answer is. It does not by itself prove that the cards are
// scrolled into view: the toBeInViewport assertions on each card do that.
const TURN1_LONG = TURN1.replace(
  "NVIDIA Data Center revenue",
  "Filler sentence to lengthen the answer. ".repeat(60) + "NVIDIA Data Center revenue",
);

// Turn 2 cites the fiscal-2024 column, so the figure the viewer marks tells
// the turns apart.
const TURN2 =
  sse("resolved", { standalone_question: STANDALONE }) +
  progress(nvdaRef) +
  sse("token", { text: "In fiscal 2024 it was $47,525 million [1]." }) +
  sse("progress", { stage: "verifying", quotes: 1 }) +
  sse("citation", {
    marker: 1,
    verified: true,
    ...nvdaRef,
    sids: [31],
    quote: "Data Center $ 47,525",
    cells: [{ sid: 31, cell: 4 }],
  }) +
  done(1, 1);

// A follow-up answer citing two filings, revealed slowly enough (3 quotes,
// ~400 ms apart) for the visitor to open one before the closing.
const TURN2_TWO_FILINGS =
  sse("resolved", { standalone_question: STANDALONE }) +
  progress(nvdaRef, msftRef) +
  sse("token", { text: "NVIDIA was $47,525 million [1]; Microsoft Cloud was $137.4 billion [2] [3]." }) +
  sse("progress", { stage: "verifying", quotes: 3 }) +
  sse("citation", {
    marker: 1,
    verified: true,
    ...nvdaRef,
    sids: [31],
    quote: "Data Center $ 47,525",
    cells: [{ sid: 31, cell: 4 }],
  }) +
  msftCitation(2) +
  msftCitation(3) +
  done(3, 3);

// Two paragraphs, one citation in each.
const TWO_PARAGRAPHS =
  progress(nvdaRef, msftRef) +
  sse("token", { text: "NVIDIA first paragraph [1].\n\nMicrosoft second paragraph [2]." }) +
  sse("progress", { stage: "verifying", quotes: 2 }) +
  nvdaCitation(1) +
  msftCitation(2) +
  done(2, 2);

const TYPED =
  progress(nvdaRef) +
  sse("token", { text: "TYPED ANSWER [1]." }) +
  sse("progress", { stage: "verifying", quotes: 1 }) +
  nvdaCitation(1) +
  done(1, 1);

const UNVERIFIED_TURN1 =
  progress(nvdaRef) +
  sse("token", { text: "NVIDIA Data Center revenue was something [1]." }) +
  sse("progress", { stage: "verifying", quotes: 1 }) +
  unverifiedCitation +
  done(1, 0);

type Body = string | (() => Promise<string>);
type AskBody = { question: string; conversation_id: string };

async function stubFilings(page: Page) {
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        { cik: 1045810, ticker: "NVDA", name: "NVIDIA Corp", filings: 12 },
        { cik: 789019, ticker: "MSFT", name: "Microsoft Corp", filings: 12 },
      ]),
    }),
  );
  for (const [accession, html, ref] of [
    [NVDA, NVDA_HTML, nvdaRef],
    [MSFT, MSFT_HTML, msftRef],
  ] as const) {
    await page.route(`${API}/filings/${accession}`, (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          accession,
          viewer_html: html,
          ticker: ref.ticker,
          form_type: ref.form_type,
          filing_date: ref.filing_date,
          period_end: ref.filing_date,
        }),
      }),
    );
  }
}

/** Stubs the API. The Nth /ask (0-based) is answered by `bodies[N]`; each request body is recorded. */
async function stub(page: Page, bodies: Body[] = [TURN1, TURN2, TYPED]) {
  const requests: AskBody[] = [];
  await stubFilings(page);
  await page.route(`${API}/ask`, async (route) => {
    const n = requests.length;
    requests.push(route.request().postDataJSON());
    const body = bodies[Math.min(n, bodies.length - 1)];
    await route.fulfill({
      contentType: "text/event-stream",
      body: typeof body === "function" ? await body() : body,
    });
  });
  return requests;
}

const welcome = (page: Page) => page.getByTestId("tour-welcome");
const callout = (page: Page) => page.getByTestId("tour-callout");
const tutorialButton = (page: Page) =>
  welcome(page).getByRole("button", { name: "Ask this question", exact: true });
const takeTutorial = (page: Page) => page.getByRole("button", { name: "Take the tutorial" });
const CLICK_STEP = "Click the pulsing [1] to see that figure in the original filing.";
const VIEWER_EMPTY = "Sources open here, with the cited sentence or figure highlighted.";
const askButton = (page: Page) => page.getByRole("button", { name: "Ask", exact: true });
const PLACEHOLDER = "e.g. Apple's net sales in fiscal 2024";
const followUpButton = (page: Page) =>
  page.getByRole("button", { name: `Ask "${FOLLOW_UP}"`, exact: true });
const chip = (page: Page) => page.getByRole("button", { name: "[1]", exact: true });
const EMPTY = "Ask a question about a filing.";

/**
 * Reloads and waits until the client has mounted. Before hydration the welcome
 * card is absent for every visitor, so asserting its absence then would pass
 * vacuously; the /companies response arrives only after the mount-time tour
 * check has run.
 */
async function reloadHydrated(page: Page) {
  expect(await page.evaluate(() => localStorage.getItem("edgar-answers.tour-seen"))).toBe("1");
  const mounted = page.waitForResponse(`${API}/companies`);
  await page.reload();
  await mounted;
}

/** Plays the tutorial from the welcome card up to the click-the-citation step. */
async function toClickStep(page: Page) {
  await page.goto("/ask");
  await tutorialButton(page).click();
  await expect(page.getByText(CLICK_STEP)).toBeVisible();
}

/** ...through the click and Next, to the "more" card. */
async function toMoreStep(page: Page) {
  await toClickStep(page);
  await chip(page).click();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText(/Want to see one more/)).toBeVisible();
}

test("the full tutorial: ask, watch, click, source, follow-up in the same conversation, close", async ({
  page,
}) => {
  let releaseFollowUp!: () => void;
  const followUpGate = new Promise<void>((resolve) => (releaseFollowUp = resolve));
  const requests = await stub(page, [
    TURN1,
    async () => {
      await followUpGate;
      return TURN2;
    },
  ]);

  await page.goto("/ask");
  // Welcome: the question, one filled call to action, and Skip tutorial.
  await expect(welcome(page)).toBeVisible();
  await expect(welcome(page).getByText(QUESTION)).toBeVisible();
  await expect(tutorialButton(page)).toHaveCount(1);
  await expect(tutorialButton(page)).toHaveClass(/bg-blue-600/);
  await expect(welcome(page).getByText("Takes under a minute.")).toBeVisible();
  await expect(welcome(page).getByRole("button")).toHaveCount(2);
  await expect(welcome(page).getByRole("button", { name: "Skip tutorial" })).toBeVisible();
  // While the tutorial runs, the header link to start it is hidden, and the
  // composer's Ask is secondary so the welcome card has the one primary button.
  await expect(takeTutorial(page)).toHaveCount(0);
  await expect(askButton(page)).not.toHaveClass(/bg-blue-600/);
  await expect(askButton(page)).toBeEnabled();
  await expect(page.getByLabel("Question")).toHaveAttribute("placeholder", PLACEHOLDER);
  await tutorialButton(page).click();

  // The panel ticks.
  await expect(page.getByText(/Watch the panel/)).toBeVisible();
  await expect(page.getByText(/Searched the filings: found 8 passages/)).toBeVisible();
  await expect(page.getByText(/Quote 1 → NVDA 10-K 2025 ✓ found word for word/)).toBeVisible();
  await expect(page.getByText(/table figure/)).toBeVisible();

  // Click callout + [1] with a pulsing ring.
  await expect(page.getByText(CLICK_STEP)).toBeVisible();
  await expect(chip(page)).toHaveAttribute("data-emphasized", "true");
  await expect(page.getByTestId("chip-ping")).toHaveCSS("animation-name", "ping");
  await chip(page).click();
  await expect(page.getByTestId("chip-ping")).toHaveCount(0);

  await expect(page.locator(".cited-figure").first()).toBeVisible();
  await expect(page.getByText(/That's the exact figure in the original filing, highlighted/)).toBeVisible();

  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(
    page.getByText(
      "Want to see one more? Follow-ups remember the conversation, so a short question is enough.",
    ),
  ).toBeVisible();
  await followUpButton(page).click();

  // Turn 1's filing must not sit next to the fiscal-2024 answer: the viewer
  // starts empty until a turn-2 citation is clicked.
  await expect(page.getByText(VIEWER_EMPTY)).toBeVisible();
  await expect(page.locator("[data-accession]")).toHaveCount(0);

  // Review Focus 1: the follow-up reuses the first turn's conversation.
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[0].question).toBe(QUESTION);
  expect(requests[1].question).toBe(FOLLOW_UP);
  expect(requests[0].conversation_id).toBeTruthy();
  expect(requests[1].conversation_id).toBe(requests[0].conversation_id);

  // While the follow-up is in flight, the pre-resolved copy shows.
  await expect(page.getByText(`Asking "${FOLLOW_UP}" in the same conversation…`)).toBeVisible();
  releaseFollowUp();
  await expect(page.getByText(/Follow-ups keep context/)).toBeVisible();
  await expect(page.getByText(`Searched for: “${STANDALONE}”`)).toBeVisible();

  // Closing: Done only — no repo link (the repo is private) and no example buttons.
  await expect(page.getByText(/works on any document collection/)).toBeVisible();
  await expect(callout(page).getByRole("link")).toHaveCount(0);
  await expect(callout(page).getByRole("button")).toHaveCount(1);
  await expect(followUpButton(page)).toHaveCount(0);
  await expect(tutorialButton(page)).toHaveCount(0);

  // The payoff, without a click: the viewer opens turn 2's first verified
  // citation, and the panel folds to its summary so the answer stays in view.
  await expect(page.locator(`[data-accession="${NVDA}"] .cited-figure`)).toHaveText("47,525");
  await expect(page.locator(`[data-accession="${NVDA}"] .cited-figure`)).toBeInViewport();
  await expect(
    page.locator("article").last().getByRole("button", { name: /^Under the hood/ }),
  ).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("article").first().getByRole("button", { name: /^Under the hood/ }))
    .toHaveAttribute("aria-expanded", "false");
  await expect(callout(page)).toBeInViewport();
  await expect(page.getByText(/In fiscal 2024 it was/)).toBeInViewport();

  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(callout(page)).toHaveCount(0);
  await expect(takeTutorial(page)).toBeVisible();
  // Done removes the closing card; the folded panel stays folded, so the
  // answer does not get pushed away as the card goes.
  await expect(
    page.locator("article").last().getByRole("button", { name: /^Under the hood/ }),
  ).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText(/In fiscal 2024 it was/)).toBeInViewport();
  await reloadHydrated(page);
  await expect(welcome(page)).toHaveCount(0);
  await expect(callout(page)).toHaveCount(0);
});

test("skipping from the welcome card leaves the regular site, for good", async ({ page }) => {
  await stub(page);
  await page.goto("/ask");
  await welcome(page).getByRole("button", { name: "Skip tutorial" }).click();

  await expect(page.getByText(EMPTY)).toBeVisible();
  // Back on the regular site the composer's Ask is the primary button again.
  await expect(askButton(page)).toHaveClass(/bg-blue-600/);
  await expect(tutorialButton(page)).toHaveCount(0);
  await expect(welcome(page)).toHaveCount(0);
  await expect(callout(page)).toHaveCount(0);

  await reloadHydrated(page);
  await expect(page.getByText(EMPTY)).toBeVisible();
  await expect(tutorialButton(page)).toHaveCount(0);
  await expect(welcome(page)).toHaveCount(0);
  await expect(callout(page)).toHaveCount(0);
});

test("skipping from the more card keeps the thread and removes every callout", async ({ page }) => {
  await stub(page);
  await toMoreStep(page);
  await callout(page).getByRole("button", { name: "Skip tutorial" }).click();

  await expect(callout(page)).toHaveCount(0);
  await expect(welcome(page)).toHaveCount(0);
  await expect(followUpButton(page)).toHaveCount(0);
  await expect(tutorialButton(page)).toHaveCount(0);
  await expect(page.getByText(QUESTION)).toBeVisible();
  await expect(page.getByText(/NVIDIA Data Center revenue was/)).toBeVisible();

  await reloadHydrated(page);
  await expect(page.getByText(EMPTY)).toBeVisible();
  await expect(welcome(page)).toHaveCount(0);
  await expect(callout(page)).toHaveCount(0);
});

test("a typed question at the welcome card leaves for the regular site, with no callouts", async ({
  page,
}) => {
  const requests = await stub(page, [TYPED]);
  await page.goto("/ask");
  await expect(welcome(page)).toBeVisible();

  await page.getByLabel("Question").fill("my own question");
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  await expect(page.getByText(/TYPED ANSWER/)).toBeVisible();
  await expect(welcome(page)).toHaveCount(0);
  await expect(callout(page)).toHaveCount(0);
  await expect(chip(page)).not.toHaveAttribute("data-emphasized", "true");
  await expect(takeTutorial(page)).toBeVisible();
  expect(requests[0].question).toBe("my own question");
  expect(await page.evaluate(() => localStorage.getItem("edgar-answers.tour-seen"))).toBe("1");
});

test("a typed question mid-tutorial never gets the scripted follow-up offered on top", async ({
  page,
}) => {
  await stub(page, [TURN1, TYPED]);
  await toClickStep(page);

  await page.getByLabel("Question").fill("my own question");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByText(/TYPED ANSWER/)).toBeVisible();
  await expect(callout(page)).toHaveCount(0);

  // Clicking through the unvetted answer stays on the regular site.
  await chip(page).last().click();
  await expect(page.locator(".cited-figure")).toBeVisible();
  await expect(callout(page)).toHaveCount(0);
  await expect(followUpButton(page)).toHaveCount(0);
});

test("a typed question at the more card finishes the tutorial", async ({ page }) => {
  const requests = await stub(page, [TURN1, TYPED]);
  await toMoreStep(page);

  await page.getByLabel("Question").fill("my own question");
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  await expect(page.getByText(/TYPED ANSWER/)).toBeVisible();
  await expect(callout(page)).toHaveCount(0);
  expect(requests[1].question).toBe("my own question");
  expect(await page.evaluate(() => localStorage.getItem("edgar-answers.tour-seen"))).toBe("1");
});

test("no verified citation on turn 1 goes straight to the closing, with the unverified badge", async ({
  page,
}) => {
  await stub(page, [UNVERIFIED_TURN1]);
  await page.goto("/ask");
  await tutorialButton(page).click();

  await expect(page.getByText(/works on any document collection/)).toBeVisible();
  await expect(page.getByText(CLICK_STEP)).toHaveCount(0);
  await expect(page.getByText("[1] unverified")).toBeVisible();
  await expect(callout(page).getByRole("button", { name: "Done", exact: true })).toBeVisible();
});

test("an error on the follow-up shows the interrupted card; Try again starts a new conversation", async ({
  page,
}) => {
  const requests = await stub(page, [
    TURN1,
    progress(nvdaRef) + sse("token", { text: "partial" }) + sse("error", { message: "The model is unavailable." }),
    TURN1,
  ]);
  await toMoreStep(page);
  await followUpButton(page).click();

  await expect(page.getByText(/That one didn't go through\./)).toBeVisible();
  await expect(page.getByText("The model is unavailable.")).toBeVisible();
  await page.getByRole("button", { name: "Try again", exact: true }).click();

  await expect.poll(() => requests.length).toBe(3);
  expect(requests[1].conversation_id).toBe(requests[0].conversation_id);
  expect(requests[2].question).toBe(QUESTION);
  expect(requests[2].conversation_id).toBeTruthy();
  expect(requests[2].conversation_id).not.toBe(requests[0].conversation_id);
  // And the tutorial resumes from the top: watch, then the click step.
  await expect(page.getByText(CLICK_STEP)).toBeVisible();
});

test("Take the tutorial from an existing thread restarts at welcome in a fresh conversation", async ({
  page,
}) => {
  const requests = await stub(page);
  await toMoreStep(page);
  await followUpButton(page).click();
  await expect(page.getByText(/works on any document collection/)).toBeVisible();

  await page.getByRole("button", { name: "Done", exact: true }).click();
  await takeTutorial(page).click();
  await expect(welcome(page)).toBeVisible();
  await expect(callout(page)).toHaveCount(0);
  await expect(page.getByText(/NVIDIA Data Center revenue was/)).toHaveCount(0);
  await expect(page.getByText(/Searched for:/)).toHaveCount(0);

  await tutorialButton(page).click();
  await expect.poll(() => requests.length).toBe(3);
  expect(requests[2].question).toBe(QUESTION);
  expect(requests[2].conversation_id).not.toBe(requests[0].conversation_id);
});

test("restarting mid-stream aborts the old stream; the new run shows only its own answer", async ({
  page,
}) => {
  let askCount = 0;
  let releaseStale!: () => void;
  const staleGate = new Promise<void>((resolve) => (releaseStale = resolve));
  await stubFilings(page);
  await page.route(`${API}/ask`, async (route) => {
    askCount += 1;
    if (askCount === 1) {
      // A slow stream that only delivers after the visitor has moved on.
      await staleGate;
      try {
        await route.fulfill({
          contentType: "text/event-stream",
          body: sse("token", { text: "STALE ANSWER" }) + done(0, 0),
        });
      } catch {
        // The aborted request is already gone: that is the point.
      }
      return;
    }
    await route.fulfill({ contentType: "text/event-stream", body: TURN1 });
  });

  await page.goto("/ask");
  await tutorialButton(page).click();
  // Mid-run the header offers "New conversation", which restarts at welcome.
  await page.getByRole("button", { name: "New conversation" }).click();
  await expect(welcome(page)).toBeVisible();
  await tutorialButton(page).click();
  await expect(page.getByText(CLICK_STEP)).toBeVisible();

  releaseStale();
  // A negative assertion needs a bounded wait: there is no event to await for
  // "a response that must never be applied". 300 ms is ample for the
  // fulfilled route to reach the (aborted) reader.
  await page.waitForTimeout(300);
  await expect(page.getByText("STALE ANSWER")).toHaveCount(0);
  await expect(page.getByText(/NVIDIA Data Center revenue was/)).toBeVisible();
  await expect(page.getByText(CLICK_STEP)).toBeVisible();
  await expect(callout(page)).toHaveCount(1);
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("the whole tutorial fits the screen and each card is brought into view", async ({ page }) => {
    await stub(page, [TURN1_LONG, TURN2]);
    const scrollWidth = () => page.evaluate(() => document.documentElement.scrollWidth);
    await page.goto("/ask");
    await expect(welcome(page)).toBeInViewport();
    await tutorialButton(page).click();
    await expect(page.getByText(CLICK_STEP)).toBeVisible();
    expect(await scrollWidth()).toBeLessThanOrEqual(375);

    await chip(page).click();
    const cited = page.locator(".cited-figure").first();
    await expect(cited).toBeVisible();
    await expect(cited).toBeInViewport();
    expect(await scrollWidth()).toBeLessThanOrEqual(375);
    // The page scrolls to the viewer, whose first child is the source card;
    // the highlight scrolls only the pane, so it never drags the page further
    // and leaves the composer above the card.
    await expect(callout(page)).toContainText("That's the exact figure");
    await expect
      .poll(async () => (await callout(page).boundingBox())?.y ?? -1, { timeout: 4000 })
      .toBeLessThan(120);
    expect((await callout(page).boundingBox())!.y).toBeGreaterThanOrEqual(0);
    await expect(page.getByLabel("Question")).not.toBeInViewport();

    // Next swaps the source card (in the viewer) for the more card (in the
    // thread, off screen): it must be scrolled into view.
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect(callout(page)).toContainText("Want to see one more");
    await expect(callout(page)).toBeInViewport();
    expect(await scrollWidth()).toBeLessThanOrEqual(375);

    await followUpButton(page).click();
    await expect(callout(page)).toContainText("works on any document collection");
    await expect(callout(page)).toBeInViewport();
    expect(await scrollWidth()).toBeLessThanOrEqual(375);
    // No auto-open on a phone: the viewer sits below and would pull the page
    // away from the closing card.
    await page.waitForTimeout(500);
    await expect(page.locator("[data-accession]")).toHaveCount(0);
    await expect(callout(page)).toBeInViewport();
  });
});

test.describe("on a touch screen", () => {
  test.use({ viewport: { width: 375, height: 812 }, hasTouch: true, isMobile: true });

  test("the click step says Tap, and the chip's hit area is at least 40 px", async ({ page }) => {
    await stub(page);
    await page.goto("/ask");
    await tutorialButton(page).click();
    await expect(
      page.getByText("Tap the pulsing [1] to see that figure in the original filing."),
    ).toBeVisible();
    const hit = await chip(page).evaluate((el) => {
      const after = getComputedStyle(el, "::after");
      return { width: parseFloat(after.width), height: parseFloat(after.height) };
    });
    expect(hit.width).toBeGreaterThanOrEqual(40);
    expect(hit.height).toBeGreaterThanOrEqual(40);
  });
});

test("answer prose is single-spaced: chips are compact badges that don't push lines apart", async ({
  page,
}) => {
  await stub(page);
  await toClickStep(page);
  const prose = page.locator("p", { hasText: "NVIDIA Data Center revenue was" });
  // 14 px x 1.55.
  await expect(prose).toHaveCSS("line-height", "21.7px");
  const chipBox = await chip(page).boundingBox();
  expect(chipBox!.height).toBeLessThanOrEqual(16);
  // No touch hit-area on a mouse pointer.
  expect(await chip(page).evaluate((el) => getComputedStyle(el, "::after").content)).toBe("none");
});

test("with reduced motion the emphasized chip keeps a still ring", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await stub(page);
  await toClickStep(page);
  await expect(page.getByTestId("chip-ping")).toBeVisible();
  await expect(page.getByTestId("chip-ping")).toHaveCSS("animation-name", "none");
  await expect(chip(page)).toHaveClass(/ring-amber-400/);
});

test("paragraphs render as separate blocks, and a marker in each still opens its source", async ({
  page,
}) => {
  await stub(page, [TWO_PARAGRAPHS]);
  await page.addInitScript(() => localStorage.setItem("edgar-answers.tour-seen", "1"));
  await page.goto("/ask");
  await page.getByLabel("Question").fill("Compare them");
  await askButton(page).click();

  const first = page.locator("p", { hasText: "NVIDIA first paragraph" });
  const second = page.locator("p", { hasText: "Microsoft second paragraph" });
  await expect(first).toBeVisible();
  await expect(second).toBeVisible();
  // Separate blocks, with a gap far smaller than a blank line (21.7 px).
  const gap = (await second.boundingBox())!.y - ((await first.boundingBox())!.y + (await first.boundingBox())!.height);
  expect(gap).toBeGreaterThan(4);
  expect(gap).toBeLessThan(12);
  await expect(first.getByRole("button", { name: "[1]", exact: true })).toBeVisible();

  await second.getByRole("button", { name: "[2]", exact: true }).click();
  await expect(page.locator(`[data-accession="${MSFT}"] .cited-sentence`)).toHaveText(
    "Microsoft Cloud revenue was $168.9 billion.",
  );
  await first.getByRole("button", { name: "[1]", exact: true }).click();
  await expect(page.locator(`[data-accession="${NVDA}"] .cited-figure`)).toHaveText("115,186");
});

test("the prerendered page has neither the tutorial link nor the regular site, so nothing flashes or jumps", async ({
  page,
  request,
}) => {
  const html = await (await request.get("/ask")).text();
  expect(html).not.toContain("Take the tutorial");
  // Nor the regular empty state: a first visit would see it pushed down and
  // restyled as the welcome card arrives, and its form could be submitted
  // natively before hydration.
  expect(html).not.toContain(EMPTY);
  expect(html).not.toContain('aria-label="Question"');
  expect(html).not.toContain("<form");

  // A first visit gets the welcome card above the composer once mounted.
  await stub(page);
  await page.goto("/ask");
  await expect(welcome(page)).toBeVisible();
  await expect(page.getByLabel("Question")).toBeVisible();
  await expect(page.getByText(EMPTY)).toHaveCount(0);
  await expect(takeTutorial(page)).toHaveCount(0);

  // A returning visitor gets the regular site and the tutorial link.
  await page.evaluate(() => localStorage.setItem("edgar-answers.tour-seen", "1"));
  await reloadHydrated(page);
  await expect(page.getByText(EMPTY)).toBeVisible();
  await expect(page.getByLabel("Question")).toBeVisible();
  await expect(takeTutorial(page)).toBeVisible();
  await expect(welcome(page)).toHaveCount(0);
});

test("a source the visitor opens during the follow-up is not switched by the closing", async ({
  page,
}) => {
  await stub(page, [TURN1, TURN2_TWO_FILINGS]);
  await toMoreStep(page);
  await followUpButton(page).click();

  // During the follow-up's reveal, open its Microsoft source.
  const article = page.locator("article").last();
  await article.getByRole("button", { name: "[2]", exact: true }).click();
  await expect(page.locator(`[data-accession="${MSFT}"] .cited-sentence`)).toBeVisible();
  await expect(page.getByText(/Follow-ups keep context/)).toBeVisible(); // still the followup step

  await expect(page.getByText(/works on any document collection/)).toBeVisible();
  await page.waitForTimeout(300);
  // Their Microsoft source is still the one showing; the payoff did not open
  // NVIDIA's figure over it.
  await expect(page.locator(`[data-accession="${MSFT}"] .cited-sentence`)).toBeVisible();
  await expect(page.locator(`[data-accession="${NVDA}"]`)).toHaveCount(0);
});

test.describe("back to the answer on a phone", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("a citation tap fills the screen with the filing; Back to answer returns to it", async ({
    page,
  }) => {
    await stub(page, [TURN1_LONG]);
    await page.addInitScript(() => localStorage.setItem("edgar-answers.tour-seen", "1"));
    await page.goto("/ask");
    await page.getByLabel("Question").fill("Compare them");
    await askButton(page).click();
    await chip(page).click();
    await expect(page.locator(".cited-figure")).toBeInViewport();

    const back = page.getByRole("button", { name: "Back to answer" });
    await expect(back).toBeInViewport();
    expect((await back.boundingBox())!.height).toBeGreaterThanOrEqual(40);
    const question = page.locator("article").last().getByText("Compare them", { exact: true });
    await expect(question).not.toBeInViewport();

    await back.click();
    await expect(question).toBeInViewport();
  });
});

test.describe("back to the answer during the tutorial on a phone", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("the source step has no Back to answer under its Next; it returns once the tutorial ends", async ({
    page,
  }) => {
    await stub(page, [TURN1_LONG]);
    await page.goto("/ask");
    await tutorialButton(page).click();
    await chip(page).click();
    await expect(page.locator(".cited-figure")).toBeInViewport();
    await expect(callout(page)).toContainText("That's the exact figure");
    // Tapping it would scroll to a thread with no card to go on from.
    await expect(page.getByRole("button", { name: "Back to answer" })).toHaveCount(0);

    await callout(page).getByRole("button", { name: "Skip tutorial" }).click();
    await expect(callout(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Back to answer" })).toBeVisible();
  });
});

test("there is no Back to answer button beside the answer on a wide screen", async ({ page }) => {
  await stub(page, [TYPED]);
  await page.addInitScript(() => localStorage.setItem("edgar-answers.tour-seen", "1"));
  await page.goto("/ask");
  await page.getByLabel("Question").fill("q");
  await askButton(page).click();
  await chip(page).click();
  await expect(page.locator(".cited-figure")).toBeVisible();
  await expect(page.getByRole("button", { name: "Back to answer" })).toBeHidden();
});
