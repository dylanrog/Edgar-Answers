"use client";

import { useMemo, useState } from "react";

import { AnswerStream } from "@/components/answer-stream";
import { AskForm } from "@/components/ask-form";
import { FilingTabs } from "@/components/filing-tabs";
import { FilingViewer } from "@/components/filing-viewer";
import { SourcesPanel } from "@/components/sources-panel";
import { initialAnswerState, reduceAnswer } from "@/lib/answer";
import type { AnswerState } from "@/lib/answer";
import { askStream } from "@/lib/api";
import type { AskFilters } from "@/lib/api";
import { groupSources } from "@/lib/sources";
import { closeTab, initialTabState, openTab } from "@/lib/tabs";
import type { Citation } from "@/lib/types";

export default function AskPage() {
  const [answer, setAnswer] = useState<AnswerState>(initialAnswerState);
  const [tabs, setTabs] = useState(initialTabState);
  const [sids, setSids] = useState<Record<string, number[]>>({});

  const groups = useMemo(() => groupSources(answer.citations), [answer.citations]);
  const labels = useMemo(
    () =>
      // The year disambiguates same-company, same-form-type filings: without
      // it, three of a company's 10-Ks render three identically-labelled tabs.
      Object.fromEntries(
        groups.map((g) => [
          g.accession,
          `${g.ticker} ${g.form_type} ${g.filing_date.slice(0, 4)}`,
        ]),
      ),
    [groups],
  );

  async function ask(question: string, filters: AskFilters) {
    setAnswer({ ...initialAnswerState, status: "streaming" });
    setTabs(initialTabState);
    setSids({});
    try {
      for await (const event of askStream(question, filters)) {
        setAnswer((previous) => reduceAnswer(previous, event));
      }
    } catch (error) {
      setAnswer((previous) => ({
        ...previous,
        status: "error",
        errorMessage:
          error instanceof Error ? error.message : "Could not reach the API.",
      }));
    }
  }

  function select(citation: Citation) {
    // Unverified and unattributable citations are inert by design (§6.3):
    // there is nothing trustworthy to scroll to.
    if (!citation.verified || citation.accession === "") return;
    setTabs((previous) => openTab(previous, citation.accession));
    setSids((previous) => ({ ...previous, [citation.accession]: citation.sids }));
  }

  return (
    <main className="grid h-screen grid-cols-[minmax(0,5fr)_minmax(0,7fr)] bg-slate-950 text-slate-200">
      <section className="overflow-y-auto border-r border-slate-800 p-5">
        <h1 className="mb-4 font-mono text-sm font-bold tracking-wide text-slate-100">
          EDGAR ANSWERS
        </h1>
        <AskForm disabled={answer.status === "streaming"} onSubmit={ask} />
        <AnswerStream state={answer} onSelect={select} />
        <SourcesPanel groups={groups} onSelect={select} />
      </section>

      <section className="flex flex-col overflow-hidden">
        <FilingTabs
          tabs={tabs}
          labels={labels}
          onActivate={(accession) => setTabs((previous) => openTab(previous, accession))}
          onClose={(accession) => setTabs((previous) => closeTab(previous, accession))}
        />
        <div className="min-h-0 flex-1 bg-slate-950">
          <FilingViewer tabs={tabs} sids={sids} />
        </div>
      </section>
    </main>
  );
}
