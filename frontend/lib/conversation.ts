const KEY = "edgar-answers.conversation-id";

// Used only when sessionStorage is unavailable: stable for the life of the
// page, so a follow-up still reaches the same conversation.
let memoryId: string | null = null;

/**
 * A random v4 UUID. crypto.randomUUID exists only in secure contexts (https
 * or localhost), so a visitor on plain http — a phone opening the dev server
 * by LAN address — would crash on the first question without the fallback;
 * crypto.getRandomValues is available on every origin.
 */
function newId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

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
    const id = newId();
    sessionStorage.setItem(KEY, id);
    return id;
  } catch {
    // Storage disabled (private mode, hardened browser): keep the id in
    // memory instead. The conversation lasts until a reload rather than the
    // whole tab session, but follow-ups within the page keep their context.
    memoryId ??= newId();
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
