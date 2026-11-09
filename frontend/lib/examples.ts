import type { AskFilters } from "./api";
import raw from "./examples.json";

export type Example = {
  id: string;
  label: string;
  question: string;
  kind: "table" | "comparison" | "prose";
  featured: boolean;
};

export const EXAMPLES: Example[] = raw as Example[];

export function featuredExample(): Example | undefined {
  return EXAMPLES.find((e) => e.featured);
}

/**
 * Examples always start a fresh conversation, so a prior turn's context can
 * never rewrite the example question into something else.
 */
export function askExampleFresh(
  example: Example,
  deps: {
    newConversation: () => void;
    ask: (question: string, filters: AskFilters) => void;
  },
): void {
  deps.newConversation();
  deps.ask(example.question, {});
}
