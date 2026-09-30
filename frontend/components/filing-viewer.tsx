"use client";

import { useEffect, useRef, useState } from "react";

import { fetchFiling } from "@/lib/api";
import { NO_HIGHLIGHT, applyHighlight } from "@/lib/highlight";
import type { Highlight } from "@/lib/highlight";
import type { TabState } from "@/lib/tabs";
import type { Filing } from "@/lib/types";

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

  // Highlighting scrolls the cited sentence into view, so it must not run
  // while the pane is hidden -- display:none elements have no layout box and
  // scrollIntoView would do nothing.
  useEffect(() => {
    if (active && filing !== null && containerRef.current !== null) {
      applyHighlight(containerRef.current, highlight.sids, highlight.cells);
    }
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
        // Safe here and only here: this HTML was sanitized by the
        // canonicalizer at ingestion, so the server is the sanitizer.
        <div
          ref={containerRef}
          className="filing-html"
          dangerouslySetInnerHTML={{ __html: filing.viewer_html }}
        />
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
