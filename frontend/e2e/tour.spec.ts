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

// A long answer pushes the cards that follow it below the fold on a phone, so
// the scroll-into-view behaviour is actually exercised.
const TURN1_LONG = TURN1.replace(
  "NVIDIA Data Center revenue",
  "Filler sentence to lengthen the answer. ".repeat(60) + "NVIDIA Data Center revenue",
);

const TURN2 =
  sse("resolved", { standalone_question: STANDALONE }) +
  progress(nvdaRef) +
  sse("token", { text: "In fiscal 2024 it was $47,525 million [1]." }) +
  sse("progress", { stage: "verifying", quotes: 1 }) +
  nvdaCitation(1) +
  done(1, 1);

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
const tutorialButton = (page: Page) => page.getByRole("button", { name: QUESTION });
const followUpButton = (page: Page) =>
  page.getByRole("button", { name: `Ask "${FOLLOW_UP}"`, exact: true });
const chip = (page: Page) => page.getByRole("button", { name: "[1]", exact: true });
const EMPTY = "Ask a question about a filing.";

/**
 * Reloads and waits until the client has mounted. The prerendered HTML already
 * has the Question input, so asserting the welcome card is absent before
 * hydration would pass vacuously; the /companies fetch fires in the same mount
 * pass as the tour check, after it.
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
  await expect(page.getByText("Click the highlighted citation")).toBeVisible();
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
  // Welcome: exactly one question button, plus Skip tutorial.
  await expect(welcome(page)).toBeVisible();
  await expect(tutorialButton(page)).toHaveCount(1);
  await expect(welcome(page).getByRole("button")).toHaveCount(2);
  await expect(welcome(page).getByRole("button", { name: "Skip tutorial" })).toBeVisible();
  await tutorialButton(page).click();

  // The panel ticks.
  await expect(page.getByText(/Watch the panel/)).toBeVisible();
  await expect(page.getByText(/Searched the filings: found 8 passages/)).toBeVisible();
  await expect(page.getByText(/Quote 1 → NVDA 10-K 2025 ✓ found word for word/)).toBeVisible();
  await expect(page.getByText(/table figure/)).toBeVisible();

  // Click callout + pulsing [1].
  await expect(page.getByText("Click the highlighted citation")).toBeVisible();
  await expect(chip(page)).toHaveClass(/animate-pulse/);
  await chip(page).click();

  await expect(page.locator(".cited-figure").first()).toBeVisible();
  await expect(page.getByText(/That's the exact figure in the original filing, highlighted/)).toBeVisible();

  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText(/Want to see one more/)).toBeVisible();
  await followUpButton(page).click();

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

  // Closing: GitHub link, Done, and no example buttons.
  await expect(page.getByText(/works on any document collection/)).toBeVisible();
  await expect(page.getByRole("link", { name: "See the code on GitHub" })).toHaveAttribute(
    "href",
    "https://github.com/dylanrog/Edgar-Answers",
  );
  await expect(callout(page).getByRole("button")).toHaveCount(1);
  await expect(followUpButton(page)).toHaveCount(0);
  await expect(tutorialButton(page)).toHaveCount(0);

  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(callout(page)).toHaveCount(0);
  await reloadHydrated(page);
  await expect(welcome(page)).toHaveCount(0);
  await expect(callout(page)).toHaveCount(0);
});

test("skipping from the welcome card leaves the regular site, for good", async ({ page }) => {
  await stub(page);
  await page.goto("/ask");
  await welcome(page).getByRole("button", { name: "Skip tutorial" }).click();

  await expect(page.getByText(EMPTY)).toBeVisible();
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
  await expect(page.getByText("Click the highlighted citation")).toHaveCount(0);
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
  await expect(page.getByText("Click the highlighted citation")).toBeVisible();
});

test("Take the tutorial from an existing thread restarts at welcome in a fresh conversation", async ({
  page,
}) => {
  const requests = await stub(page);
  await toMoreStep(page);
  await followUpButton(page).click();
  await expect(page.getByText(/works on any document collection/)).toBeVisible();

  await page.getByRole("button", { name: "Take the tutorial" }).click();
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
  await page.getByRole("button", { name: "Take the tutorial" }).click();
  await expect(welcome(page)).toBeVisible();
  await tutorialButton(page).click();
  await expect(page.getByText("Click the highlighted citation")).toBeVisible();

  releaseStale();
  // A negative assertion needs a bounded wait: there is no event to await for
  // "a response that must never be applied". 300 ms is ample for the
  // fulfilled route to reach the (aborted) reader.
  await page.waitForTimeout(300);
  await expect(page.getByText("STALE ANSWER")).toHaveCount(0);
  await expect(page.getByText(/NVIDIA Data Center revenue was/)).toBeVisible();
  await expect(page.getByText("Click the highlighted citation")).toBeVisible();
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
    await expect(page.getByText("Click the highlighted citation")).toBeVisible();
    expect(await scrollWidth()).toBeLessThanOrEqual(375);

    await chip(page).click();
    const cited = page.locator(".cited-figure").first();
    await expect(cited).toBeVisible();
    await expect(cited).toBeInViewport();
    expect(await scrollWidth()).toBeLessThanOrEqual(375);

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
  });
});
