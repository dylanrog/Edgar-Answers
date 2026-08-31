const KEY = "edgar-answers.conversation-id";

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
    // Storage disabled (private mode, hardened browser): fall back to a
    // per-call id. The conversation won't persist between questions — no
    // worse than the pre-memory single-shot behaviour.
    return crypto.randomUUID();
  }
}

export function startNewConversation(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // Nothing to clear when storage is unavailable.
  }
}
