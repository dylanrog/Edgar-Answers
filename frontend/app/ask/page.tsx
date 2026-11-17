"use client";

import { ClassicAsk } from "@/components/classic-ask";
import { EvidenceAsk } from "@/components/evidence/evidence-ask";

// Inlined at build time. Set NEXT_PUBLIC_LAYOUT=classic in the deploy's env and
// rebuild to put the original layout back on /ask without a code change.
const LAYOUT = process.env.NEXT_PUBLIC_LAYOUT;

export default function AskPage() {
  return LAYOUT === "classic" ? <ClassicAsk /> : <EvidenceAsk />;
}
