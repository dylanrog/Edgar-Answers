import type { Company } from "./types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Trailing words of a legal name that a visitor doesn't need:
// "JPMorgan Chase & Co." -> "JPMorgan Chase", "Amazon.com, Inc." -> "Amazon".
const SUFFIX = /(?:,|\s)+(?:Inc\.?|Corporation|Corp\.?|Co\.?|Company|Platforms|Holdings|Ltd\.?|plc|&)$/i;

export function shortName(name: string): string {
  let short = name.trim();
  for (let previous = ""; previous !== short; ) {
    previous = short;
    short = short.replace(SUFFIX, "").replace(/\.com$/i, "").trim();
  }
  return short || name;
}

function joinAnd(items: string[]): string {
  return items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

// "2023-08-04" -> year 2023, month index 7. Read from the string, not a Date,
// so the visitor's time zone can't shift a month boundary.
function yearMonth(iso: string): [number, number] {
  return [Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1];
}

/**
 * What the corpus covers, as one sentence for the empty state: "We have 10-K
 * and 10-Q filings from Apple, … and Walmart, filed in the last 3 years
 * (Aug 2023 – Jul 2026)." Null when the API didn't send coverage fields, so
 * the caller can fall back to a generic prompt.
 */
export function coverageSentence(companies: Company[]): string | null {
  if (companies.length === 0) return null;
  if (companies.some((c) => !c.form_types?.length || !c.first_filed || !c.last_filed)) return null;

  const forms = [...new Set(companies.flatMap((c) => c.form_types ?? []))].sort();
  const first = companies.map((c) => c.first_filed as string).sort()[0];
  const last = companies.map((c) => c.last_filed as string).sort().at(-1) as string;
  const [y0, m0] = yearMonth(first);
  const [y1, m1] = yearMonth(last);
  const years = Math.max(1, Math.round(((y1 - y0) * 12 + (m1 - m0) + 1) / 12));
  const span = years === 1 ? "the last year" : `the last ${years} years`;
  const range = `${MONTHS[m0]} ${y0} – ${MONTHS[m1]} ${y1}`;

  return (
    `We have ${joinAnd(forms)} filings from ${joinAnd(companies.map((c) => shortName(c.name)))}, ` +
    `filed in ${span} (${range}).`
  );
}
