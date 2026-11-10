import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const ACCESSION = "0001045810-25-000023";
const API = "http://localhost:8000";

const sse = (event: string, data: unknown) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

const ANSWER =
  sse("token", { text: "Data Center revenue was $115,186 million [1]." }) +
  sse("citation", {
    marker: 1,
    verified: true,
    accession: ACCESSION,
    ticker: "NVDA",
    form_type: "10-K",
    filing_date: "2025-02-26",
    sids: [31],
    quote: "Data Center $ 115,186",
    cells: [{ sid: 31, cell: 2 }],
  }) +
  sse("done", {
    chunks_retrieved: 8,
    citations_total: 1,
    citations_verified: 1,
    unverified_answer: false,
  });

const filler = (from: number, n: number) =>
  Array.from(
    { length: n },
    (_, i) => `<p><span data-sid="${from + i}">Filler sentence ${from + i} of the filing.</span></p>`,
  ).join("");

// The shape of a real 10-K (NVDA FY2025): an EDGAR-relative graph image sits
// above the cited table, with enough text above it that centring the figure
// scrolls the pane well past the image.
const VIEWER_HTML =
  `<p style="text-align: justify"><span data-sid="0">A justified paragraph, as filings set their body text.</span></p>` +
  filler(1, 25) +
  `<img src="missing.jpg" style="height:500px;display:block">` +
  `<table><tbody>
     <tr data-sid="30"><td>Year Ended</td></tr>
     <tr data-sid="31"><td style="text-align: justify">Data Center</td><td>$</td><td>115,186</td><td>$</td><td>47,525</td></tr>
   </tbody></table>` +
  filler(100, 40);

async function stub(page: Page) {
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({ contentType: "application/json", body: "[]" }),
  );
  await page.route(`${API}/ask`, (route) =>
    route.fulfill({ contentType: "text/event-stream", body: ANSWER }),
  );
  await page.route(`${API}/filings/${ACCESSION}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        accession: ACCESSION,
        viewer_html: VIEWER_HTML,
        ticker: "NVDA",
        form_type: "10-K",
        filing_date: "2025-02-26",
        period_end: "2025-01-26",
      }),
    }),
  );
  // The image 404s at once, as an EDGAR-relative src does against this
  // origin: the failure lands while the highlight's smooth scroll is still
  // running, so the browser's scroll anchoring does not compensate for the
  // content that vanishes above the figure.
  const imageRequests: string[] = [];
  await page.route("**/missing.jpg", (route) => {
    imageRequests.push(route.request().url());
    return route.fulfill({ status: 404, body: "" });
  });
  return imageRequests;
}

async function openCitation(page: Page) {
  await page.addInitScript(() => localStorage.setItem("edgar-answers.tour-seen", "1"));
  await page.goto("/ask");
  await page.getByLabel("Question").fill("What was Data Center revenue?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await page.getByRole("button", { name: "[1]", exact: true }).click();
  const figure = page.locator(`[data-accession="${ACCESSION}"] td.cited-figure`);
  await expect(figure).toHaveText("115,186");
  return figure;
}

test("a broken filing image above the cited figure never scrolls it out of view", async ({
  page,
}) => {
  const imageRequests = await stub(page);
  const figure = await openCitation(page);

  // Long enough for the 404 and the smooth scroll to finish: the layout the
  // visitor is left with, not the first frame.
  await page.waitForTimeout(1500);
  // toBeInViewport clips by every scrolling ancestor, so a figure scrolled
  // above the viewer pane fails here even though the page itself is unscrolled.
  await expect(figure).toBeInViewport();
  await expect(page.locator(`[data-accession="${ACCESSION}"] img`)).toHaveCount(0);
  expect(imageRequests).toEqual([]);
});

test("the cited figure is re-centred when the layout shifts just after the click", async ({
  page,
}) => {
  await stub(page);
  const figure = await openCitation(page);
  await expect(figure).toBeInViewport();

  // Something above the figure grows after the highlight scrolled (a late
  // font, a card mounting): the figure must be brought back to centre.
  await page.evaluate((accession) => {
    const html = document.querySelector(`[data-accession="${accession}"] .filing-html`);
    const spacer = document.createElement("div");
    spacer.style.height = "900px";
    html?.querySelector("table")?.before(spacer);
  }, ACCESSION);
  await expect(figure).toBeInViewport();
});

test("a freshly opened figure pulses briefly", async ({ page }) => {
  await stub(page);
  const figure = await openCitation(page);
  await expect(figure).toHaveCSS("animation-name", "cited-pulse");
  // Two short cycles, then the steady highlight alone.
  await expect(figure).not.toHaveClass(/cited-pulse/, { timeout: 4000 });
  await expect(figure).toHaveClass(/cited-figure/);
});

test("with reduced motion the figure does not pulse", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await stub(page);
  const figure = await openCitation(page);
  await expect(figure).toHaveCSS("animation-name", "none");
  await expect(figure).toHaveClass(/cited-figure/);
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("justified filing text is left-aligned; table cells keep their alignment", async ({
    page,
  }) => {
    await stub(page);
    await openCitation(page);
    const pane = page.locator(`[data-accession="${ACCESSION}"]`);
    await expect(pane.locator("p", { hasText: "A justified paragraph" })).toHaveCSS(
      "text-align",
      "left",
    );
    await expect(pane.locator("td", { hasText: "Data Center" })).toHaveCSS(
      "text-align",
      "justify",
    );
  });
});

test("on a wide screen the filing keeps its justification", async ({ page }) => {
  await stub(page);
  await openCitation(page);
  await expect(
    page.locator(`[data-accession="${ACCESSION}"] p`, { hasText: "A justified paragraph" }),
  ).toHaveCSS("text-align", "justify");
});
