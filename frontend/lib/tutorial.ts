import type { AskFilters } from "./api";
import tutorial from "./tutorial.json";

export const TUTORIAL: { question: string; followUp: string } = tutorial;

/** Asks `question` in a brand-new conversation, with no filters. */
export function askFresh(
  question: string,
  deps: {
    newConversation: () => void;
    ask: (q: string, f: AskFilters) => void;
  },
): void {
  deps.newConversation();
  deps.ask(question, {});
}
