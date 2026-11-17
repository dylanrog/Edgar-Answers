import { describe, expect, it } from "vitest";
import { coverageSentence, shortName } from "../coverage";
import type { Company } from "../types";

const company = (ticker: string, name: string, first: string, last: string, forms = ["10-K", "10-Q"]): Company => ({
  cik: 1,
  ticker,
  name,
  filings: 12,
  form_types: forms,
  first_filed: first,
  last_filed: last,
});

// The live corpus's legal names, as /companies returns them.
const CORPUS = [
  ["AAPL", "Apple Inc."],
  ["AMZN", "Amazon.com, Inc."],
  ["GOOGL", "Alphabet Inc."],
  ["JNJ", "Johnson & Johnson"],
  ["JPM", "JPMorgan Chase & Co."],
  ["META", "Meta Platforms, Inc."],
  ["MSFT", "Microsoft Corporation"],
  ["NVDA", "NVIDIA Corporation"],
  ["TSLA", "Tesla, Inc."],
  ["WMT", "Walmart Inc."],
].map(([t, n]) => company(t, n, "2023-10-27", "2026-07-31"));

describe("shortName", () => {
  it("drops the legal suffixes of every company in the corpus", () => {
    expect(CORPUS.map((c) => shortName(c.name))).toEqual([
      "Apple", "Amazon", "Alphabet", "Johnson & Johnson", "JPMorgan Chase",
      "Meta", "Microsoft", "NVIDIA", "Tesla", "Walmart",
    ]);
  });

  it("keeps a name that is nothing but a suffix", () => {
    expect(shortName("Inc.")).toBe("Inc.");
  });
});

describe("coverageSentence", () => {
  it("names the forms, the companies and the span of the live corpus", () => {
    const corpus = [{ ...CORPUS[0], first_filed: "2023-08-04" }, ...CORPUS.slice(1)];
    expect(coverageSentence(corpus)).toBe(
      "We have 10-K and 10-Q filings from Apple, Amazon, Alphabet, Johnson & Johnson, " +
        "JPMorgan Chase, Meta, Microsoft, NVIDIA, Tesla and Walmart, " +
        "filed in the last 3 years (Aug 2023 – Jul 2026).",
    );
  });

  it("takes the earliest first filing and the latest last filing across companies", () => {
    const s = coverageSentence([
      company("A", "A Inc.", "2024-03-01", "2025-01-15", ["10-Q"]),
      company("B", "B Inc.", "2023-02-10", "2024-12-01", ["10-K"]),
    ]);
    expect(s).toBe("We have 10-K and 10-Q filings from A and B, filed in the last 2 years (Feb 2023 – Jan 2025).");
  });

  it("says 'the last year' for a single year of filings and handles one company", () => {
    expect(coverageSentence([company("A", "Acme Corp", "2025-02-01", "2025-11-30", ["10-Q"])])).toBe(
      "We have 10-Q filings from Acme, filed in the last year (Feb 2025 – Nov 2025).",
    );
  });

  it("returns null when the API sent no coverage fields, so the page can fall back", () => {
    expect(coverageSentence([{ cik: 1, ticker: "AAPL", name: "Apple Inc.", filings: 13 }])).toBeNull();
    expect(coverageSentence([])).toBeNull();
  });
});
