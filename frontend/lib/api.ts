import { parseSSE } from "./sse";
import type { Company, Filing, SSEEvent } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type AskFilters = { ticker?: string; tickers?: string[]; form_type?: string };

/**
 * Stream POST /ask as decoded SSE events.
 *
 * The browser's built-in EventSource cannot do this: it is GET-only, and /ask
 * takes a JSON body. So we read response.body ourselves and hand each chunk to
 * parseSSE, carrying its unconsumed remainder into the next iteration.
 */
export async function* askStream(
  question: string,
  filters: AskFilters = {},
  conversationId?: string,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const response = await fetch(`${API_URL}/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, filters, conversation_id: conversationId }),
    signal,
  });
  if (!response.ok || response.body === null) {
    throw new Error(`Ask failed with status ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // stream: true so a multi-byte character split across chunks survives.
      buffer += decoder.decode(value, { stream: true });
      const { events, rest } = parseSSE(buffer);
      buffer = rest;
      for (const event of events) yield event;
    }
    // Final flush: without it a multi-byte character ending the stream is
    // dropped, since every decode above deferred incomplete sequences.
    buffer += decoder.decode();
    for (const event of parseSSE(buffer).events) yield event;
  } finally {
    // A consumer that stops early (unmount, thrown error, `break`) triggers
    // the generator's return path. Without this the response body stays open
    // and the connection leaks.
    await reader.cancel().catch(() => {});
  }
}

export async function fetchFiling(accession: string): Promise<Filing> {
  // Encoded even though accessions are digits and dashes: the value reaches
  // here from a server payload, and building URLs by raw interpolation is the
  // habit worth not having.
  const response = await fetch(`${API_URL}/filings/${encodeURIComponent(accession)}`);
  if (!response.ok) throw new Error(`Filing ${accession} unavailable`);
  return (await response.json()) as Filing;
}

export async function fetchCompanies(): Promise<Company[]> {
  const response = await fetch(`${API_URL}/companies`);
  if (!response.ok) throw new Error("Could not load companies");
  return (await response.json()) as Company[];
}
