"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { fetchFiling } from "@/lib/api";
import { NO_HIGHLIGHT, applyHighlight, keepCentred } from "@/lib/highlight";
import type { Highlight } from "@/lib/highlight";
import type { TabState } from "@/lib/tabs";
import type { Filing } from "@/lib/types";
import { parseViewerHtml } from "@/lib/viewer-html";

/**
 * One mounted filing. Kept in the DOM while its tab is open even when
 * inactive -- hidden with display:none rather than unmounted -- so that its
 * scroll position survives a tab switch. Unmounting would also mean
 * re-fetching and re-parsing ~800 KB of HTML on every switch.
 */
function FilingPane({
  accession,
  highlight,
  active,
}: {
  accession: string;
  highlight: Highlight;
  active: boolean;
}) {
  const [filing, setFiling] = useState<Filing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    fetchFiling(accession)
      .then((next) => {
        if (!cancelled) setFiling(next);
      })
      .catch(() => {
        if (!cancelled) setError("Filing not available.");
      });
    return () => {
      cancelled = true;
    };
  }, [accession]);

  // Inject the HTML imperatively so React never manages these children: with
  // dangerouslySetInnerHTML a parent re-render could replace them and wipe
  // the injected highlight marks.
  useLayoutEffect(() => {
    if (filing !== null && containerRef.current !== null) {
      const container = containerRef.current;
      // Safe here and only here: this HTML was sanitized by the
      // canonicalizer at ingestion, so the server is the sanitizer.
      // Images that cannot load are dropped before the HTML goes live, so
      // nothing above the cited figure collapses after it is scrolled to.
      container.replaceChildren(parseViewerHtml(filing.viewer_html));
    }
  }, [filing]);

  // Highlighting scrolls the cited sentence into view, so it must not run
  // while the pane is hidden -- display:none elements have no layout box and
  // scrollIntoView would do nothing.
  useEffect(() => {
    const container = containerRef.current;
    if (!active || filing === null || container === null) return;
    const target = applyHighlight(container, highlight.sids, highlight.cells);
    if (target === null) return;
    // Watch the scrolling pane as well as the HTML: a card mounting above
    // the viewer shrinks the pane without resizing the HTML.
    const watched = [container, container.parentElement].filter(
      (el): el is HTMLElement => el !== null,
    );
    return keepCentred(target, watched);
  }, [active, filing, highlight]);

  return (
    // data-accession gives each pane a stable handle regardless of which is
    // active — the e2e scroll-persistence spec needs to address a *specific*
    // pane, and a class selector would resolve to whichever is visible.
    <div
      data-accession={accession}
      className={active ? "h-full overflow-y-auto p-5" : "hidden"}
    >
      {error !== null && <p className="text-red-400">{error}</p>}
      {error === null && filing === null && (
        <p className="text-slate-500">Loading filing…</p>
      )}
      {filing !== null && (
        <div ref={containerRef} className="filing-html" />
      )}
    </div>
  );
}

export function FilingViewer({
  tabs,
  highlights,
}: {
  tabs: TabState;
  highlights: Record<string, Highlight>;
}) {
  if (tabs.open.length === 0) {
    return (
      <p className="p-5 text-slate-500">Click a source to open the filing here.</p>
    );
  }

  return (
    <>
      {tabs.open.map((accession) => (
        <FilingPane
          key={accession}
          accession={accession}
          highlight={highlights[accession] ?? NO_HIGHLIGHT}
          active={accession === tabs.active}
        />
      ))}
    </>
  );
}
