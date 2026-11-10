const KEY = "edgar-answers.conversation-id";

// Used only when sessionStorage is unavailable: stable for the life of the
// page, so a follow-up still reaches the same conversation.
let memoryId: string | null = null;

/**
 * A conversation is one browser session: sessionStorage survives a reload
 * but not a tab close, which matches "no chat-history across sessions" as
 * the honest default rather than accumulating history forever. The id is a
 * grouping key the server trusts as-is — there is no account behind it.
 */
export function getOrCreateConversationId(): string {
  try {
    const existing = sessionStorage.getItem(KEY);
    if (existing) return existing;
    const id = crypto.randomUUID();
    sessionStorage.setItem(KEY, id);
    return id;
  } catch {
    // Storage disabled (private mode, hardened browser): keep the id in
    // memory instead. The conversation lasts until a reload rather than the
    // whole tab session, but follow-ups within the page keep their context.
    memoryId ??= crypto.randomUUID();
    return memoryId;
  }
}

export function startNewConversation(): void {
  memoryId = null;
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // Nothing to clear when storage is unavailable.
  }
}
