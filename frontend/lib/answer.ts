import type { Citation, SSEEvent } from "./types";

export type AnswerState = {
  prose: string;
  citations: Map<number, Citation>;
  status: "idle" | "streaming" | "done" | "error";
  /** Set only when the model's citation block never parsed (design §10). */
  notice: string | null;
  /** Set only when status is "error". */
  errorMessage: string | null;
  chunksRetrieved: number | null;
  /** The rewritten, self-contained question used for retrieval — set only
   *  when a follow-up was rewritten (design §6.4 `resolved` event). */
  standaloneQuestion: string | null;
};

export const initialAnswerState: AnswerState = {
  prose: "",
  citations: new Map(),
  status: "idle",
  notice: null,
  errorMessage: null,
  chunksRetrieved: null,
  standaloneQuestion: null,
};

const UNVERIFIED_NOTICE =
  "The model did not return a usable citation block, so this answer is unverified.";

/** Pure state transition for one SSE event. Never mutates `state`. */
export function reduceAnswer(state: AnswerState, event: SSEEvent): AnswerState {
  switch (event.event) {
    case "token": {
      const { text } = event.data as { text: string };
      return { ...state, prose: state.prose + text, status: "streaming" };
    }
    case "citation": {
      const raw = event.data as Citation;
        // Older servers and hand-written fixtures omit cells; normalize once here
        // so every consumer can rely on an array.
        const citation: Citation = { ...raw, cells: raw.cells ?? [] };
      // A fresh Map, because React compares by reference to decide re-renders.
      const citations = new Map(state.citations);
      citations.set(citation.marker, citation);
      return { ...state, citations };
    }
    case "done": {
      const data = event.data as {
        chunks_retrieved: number;
        unverified_answer: boolean;
      };
      return {
        ...state,
        status: "done",
        chunksRetrieved: data.chunks_retrieved,
        notice: data.unverified_answer ? UNVERIFIED_NOTICE : null,
      };
    }
    case "error": {
      const { message } = event.data as { message: string };
      // Deliberately keeps `prose`: a partial answer is more useful than a
      // blank pane, and design §10 says an outage is reported, not hidden.
      return { ...state, status: "error", errorMessage: message };
    }
    case "resolved": {
      const { standalone_question } = event.data as { standalone_question: string };
      return { ...state, standaloneQuestion: standalone_question };
    }
    default:
      return state;
  }
}
