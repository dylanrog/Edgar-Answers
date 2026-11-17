// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { buildExcerpt } from "../excerpt";

function filing(html: string): DocumentFragment {
  const template = document.createElement("template");
  template.innerHTML = html;
  return template.content;
}

const FILING = filing(`
  <p><span data-sid="1">Revenue was $130.5 billion.</span> <span data-sid="2">Data Center was up 142%.</span>
  <span data-sid="3">Gaming was up 9%.</span></p>
  <table>
    <tr><th></th><th>2025</th><th>2024</th></tr>
    <tr data-sid="4"><td>Data Center</td><td>115,186</td><td>47,525</td></tr>
    <tr data-sid="5"><td>Gaming</td><td>11,350</td><td>10,447</td></tr>
  </table>
  <p><span data-sid="6">Automotive was up 55%.</span></p>
`);

const text = (fragment: DocumentFragment | null) =>
  fragment === null ? null : (fragment.textContent ?? "").replace(/\s+/g, " ").trim();

describe("buildExcerpt", () => {
  it("gives a cited table row its whole table, header row included", () => {
    const excerpt = buildExcerpt(FILING, [4]);
    expect(excerpt?.querySelectorAll("table")).toHaveLength(1);
    expect(excerpt?.querySelectorAll("tr")).toHaveLength(3);
    expect(excerpt?.querySelector('tr[data-sid="4"]')).not.toBeNull();
  });

  it("gives a cited sentence one neighbour either side", () => {
    expect(text(buildExcerpt(FILING, [2]))).toBe(
      "Revenue was $130.5 billion. Data Center was up 142%. Gaming was up 9%.",
    );
  });

  it("keeps a window at the start of the filing to what exists", () => {
    expect(text(buildExcerpt(FILING, [1]))).toBe("Revenue was $130.5 billion. Data Center was up 142%.");
  });

  it("leaves neighbouring table rows out of a prose window", () => {
    expect(text(buildExcerpt(FILING, [6]))).toBe("Automotive was up 55%.");
  });

  it("skips a neighbour with no words, such as a list bullet", () => {
    const bullets = filing(
      '<p><span data-sid="1">•</span> <span data-sid="2">Cloud revenue was $168.9 billion.</span> <span data-sid="3">•</span></p>',
    );
    expect(text(buildExcerpt(bullets, [2]))).toBe("Cloud revenue was $168.9 billion.");
  });

  it("keeps data-sid on the clones so the highlight can find them", () => {
    expect(buildExcerpt(FILING, [2])?.querySelector('[data-sid="2"]')).not.toBeNull();
  });

  it("does not move nodes out of the filing", () => {
    buildExcerpt(FILING, [4]);
    expect(FILING.querySelector('tr[data-sid="4"]')).not.toBeNull();
  });

  it("keeps the prose part of a citation that spans a table row and a sentence", () => {
    const excerpt = buildExcerpt(FILING, [4, 6]);
    expect(excerpt?.querySelectorAll("table")).toHaveLength(1);
    expect(excerpt?.querySelector('span[data-sid="6"]')).not.toBeNull();
  });

  it("gives a row of a nested table the outer table, and strips ids from the clones", () => {
    const nested = filing(
      '<table id="outer"><tr><td>Header</td></tr><tr><td><table><tr data-sid="7"><td>Row</td></tr></table></td></tr></table>',
    );
    const excerpt = buildExcerpt(nested, [7]);
    expect(excerpt?.textContent).toContain("Header");
    expect(excerpt?.querySelector("[id]")).toBeNull();
  });

  it("returns null for no sids or sids the filing lacks", () => {
    expect(buildExcerpt(FILING, [])).toBeNull();
    expect(buildExcerpt(FILING, [99])).toBeNull();
  });
});
