# Conversation Memory — Design

**Date:** 2026-08-30
**Status:** proposed
**Part of:** new subsystem. Reverses `design.md` §2's locked v1 scope
decision "Auth, chat history, threading | Out | Not what this project is
for" — that row must be updated when this ships. Conversation history is
already named in §14's v2+ backlog, so this is scheduled scope-expansion,
not scope creep.
**Depends on:** none structurally, but composes with
`2026-08-30-per-target-query-rewriting-design.md` (see §3.4) — build order
is a project choice, not a hard dependency either direction.

## 1. Problem

`/ask` is fully stateless today: `answer_stream` takes one `question` and
has no notion of a prior turn. A user cannot ask "and what about last
year?" or "how does that compare to Amazon?" as a follow-up — each question
must be entirely self-contained, or `resolve_targets`/`retrieve_for_targets`
have nothing to resolve pronouns or ellipsis against and will search on
text that doesn't name what the user means.

## 2. Scope

**In scope:** anonymous, per-browser conversation continuity (no user
accounts, no auth); persisting each turn (question, resolved answer,
resolved tickers); rewriting a follow-up into a standalone question using
the last few turns before it enters the existing retrieval pipeline
unchanged; a threaded chat UI on `/ask`; a "new conversation" action.

**Out of scope:** user accounts or login (still a `design.md` §2 non-goal
— a client-generated id is not an account); cross-device sync (a
conversation lives in one browser's storage; there is no server-side way
to look one up without knowing its id); editing or deleting past turns;
any change to citation verification, highlighting, or the chunk/sentence
data model — beyond the follow-up rewrite, its one `resolved` SSE event
(§3.5), and a persistence side effect, nothing in the
retrieve → generate → verify path changes.

## 3. Design

### 3.1 Identity: a client-generated id, not an account

A conversation is identified by a UUID the **frontend** generates
(`crypto.randomUUID()`) and stores in `sessionStorage` — new tab or new
session gets a new conversation, matching "no chat-history across
sessions" as the honest default rather than silently accumulating history
forever. A "New conversation" button clears it explicitly. There is no
server-side session, cookie, or login: the id is just a grouping key,
functionally identical to how `accession` groups sentences today. This is
the least amount of state that makes multi-turn follow-ups possible
without reversing the *auth* half of the §2 non-goal — only the
*chat-history* half is what this spec actually needs to reverse.

### 3.2 Storage: one new table

```sql
CREATE TABLE conversation_turns (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_index INT NOT NULL,
    question TEXT NOT NULL,             -- what the user actually typed
    standalone_question TEXT NOT NULL,  -- the rewritten, self-contained form used for retrieval
    answer_text TEXT NOT NULL,          -- the full streamed answer, reassembled
    tickers TEXT[] NOT NULL DEFAULT '{}',  -- resolved Target tickers, for the next turn's context and for debugging
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, turn_index)
);
CREATE INDEX conversation_turns_conversation_id_idx
    ON conversation_turns (conversation_id, turn_index);
```

`conversation_id` is `TEXT`, not Postgres `UUID` — consistent with
`accession`/`ticker` already being plain `TEXT` elsewhere in this schema
rather than a domain type, and it avoids a format-validation boundary this
spec doesn't need. No `conversations` parent table: nothing today needs to
list "all conversations," so a bare grouping key on `conversation_turns` is
the whole requirement (YAGNI — add a parent table if and when something
needs to enumerate conversations).

Migration: `backend/migrations/003_conversation_turns.sql`.

### 3.3 New module: `backend/src/api/conversation.py`

```python
@dataclass(frozen=True)
class Turn:
    question: str
    standalone_question: str
    answer_text: str
    tickers: list[str]


def load_recent_turns(conn, conversation_id: str, *, limit: int = 3) -> list[Turn]:
    """Most recent turns first N, returned oldest-first for prompt order."""
    ...


def save_turn(
    conn, conversation_id: str,
    question: str, standalone_question: str, answer_text: str, tickers: list[str],
) -> None:
    """Appends a turn. `turn_index` is computed internally as the count of
    existing rows for this conversation_id (0 for the first turn, 1 for the
    second, ...) -- the caller never passes it. This matters because
    `load_recent_turns` returns at most the last 3 turns: a caller deriving
    the index from `len(history)` would repeat turn_index 0-2 forever once a
    conversation passed 3 turns, colliding with the UNIQUE constraint."""
    ...


class ConversationRewriter(Protocol):
    def resolve(self, question: str, history: list[Turn]) -> str:
        """Return a standalone version of `question`, given prior turns."""
        ...


def parse_resolved_question(raw: str, fallback: str) -> str:
    """Same defensive-parse contract as the rest of the detection layer:
    malformed JSON, no JSON object, or a blank `question` field all degrade
    to `fallback` (the follow-up exactly as typed) -- a resolution failure
    must never make the question less answerable than not resolving it."""
    ...


@dataclass
class AnthropicConversationRewriter:
    model: str = MODEL
    max_tokens: int = 256
    api_key: str | None = None

    def resolve(self, question: str, history: list[Turn]) -> str: ...
```

Prompt shape:

```
You rewrite a follow-up question into a standalone question, using the
conversation so far.

You will be given the prior turns (question and answer) and a new
follow-up. If the follow-up references something from the prior turns
(a pronoun, an implied company or period, "what about X instead"),
rewrite it to be fully self-contained. If the follow-up is already
self-contained, return it unchanged.

Respond with strict JSON and nothing else:
{"question": "What was Apple's revenue in fiscal 2023?"}
```

`load_recent_turns` caps at the last 3 turns (bounds both the prompt's
token growth and this call's latency as a conversation gets long; 3 was
chosen as enough for the common "and last year?" / "what about X" cases
without unbounded growth — revisit if real usage shows follow-ups
referencing further back).

### 3.4 Wiring into `answer_stream`

```python
def answer_stream(
    conn, embedder, generator, company_detector, question, *,
    tickers=None, form_type=None, k_final=8,
    period_detector=None, query_rewriter=None,     # existing / spec-1 params
    conversation_id: str | None = None,
    conversation_rewriter: ConversationRewriter | None = None,
) -> Iterator[AnswerEvent]:
```

At the top, before `resolve_targets`:

```python
history = load_recent_turns(conn, conversation_id) if conversation_id else []
standalone_question = question
if history and conversation_rewriter is not None:
    try:
        standalone_question = conversation_rewriter.resolve(question, history)
    except Exception as exc:  # noqa: BLE001 -- degrades to the raw follow-up
        logger.warning(
            "conversation_rewriter.resolve failed, using the raw question: %s", exc
        )
```

The `try` wraps only the rewriter call, not `load_recent_turns` — narrowly
scoped the same way `resolve_targets` wraps only `.detect()` calls, not the
DB query that precedes them (a DB bug should surface as a real error, not
be swallowed as "no history").

Every downstream call (`resolve_targets`, `retrieve_for_targets`,
`build_user_message`) uses `standalone_question` in place of `question`.
This is the entire integration point: nothing in `targets.py`, `verify.py`,
or `generate.py` changes, because they already only ever see "the question
string to answer" — memory just changes which string that is, exactly the
same shape of change spec 1 makes at the per-target level. `resolve_targets`
still receives an optional `query_rewriter`, unaffected — the two rewrites
compose in sequence: raw follow-up -> [conversation rewrite] ->
`standalone_question` -> company/period detection -> targets -> [per-target
query rewrite] -> `search_query` per target -> `retrieve()`.

A rewriter exception, or no `conversation_rewriter` wired, degrades to
`standalone_question = question` — same "a detector failure never breaks
the request" contract as every other detector in this codebase.

When `standalone_question` differs from `question`, `answer_stream` yields
one `AnswerEvent("resolved", {"standalone_question": standalone_question})`
before `resolve_targets` runs (see §3.5) — the single point where the
rewritten form leaves the backend. An unchanged follow-up (rewriter
returned the question as-is, degraded, or was never wired) yields no
event.

At the end (same place `done` is currently yielded, inside the `try` — so
a turn that errors mid-stream is **not** persisted), accumulate the
streamed answer text (`answer_parts: list[str]`, appended alongside every
`token` event already yielded) and, if `conversation_id` was given:

```python
save_turn(
    conn, conversation_id,
    question=question, standalone_question=standalone_question,
    answer_text="".join(answer_parts),
    tickers=[t.ticker for t in targets],
)
```

A turn is persisted whether or not its citation block parsed — an
unverified answer is still a real turn the next follow-up may refer to.
Only a hard `error` event (LLM outage, DB failure) skips the save.

### 3.5 API contract

`AskRequest` gains a top-level `conversation_id: str | None = None` —
sibling to `filters`, not inside it: this is a session/thread concept, not
a retrieval filter. `app.py` gains `get_conversation_rewriter()` (same DI
shape as the existing detector providers) and passes both the id and
rewriter through to `answer_stream`.

One new SSE event, `resolved`, carries the rewritten question to the
client so the UI can show what was actually searched (§3.6). It is
emitted once, before the first `token` event, and **only when
`standalone_question != question`** — an unchanged follow-up produces no
event. Shape:

```
event: resolved
data: {"standalone_question": "What was Apple's revenue in fiscal 2023?"}
```

This is the only backend change to the SSE contract; `token`, `citation`,
`done`, and `error` are untouched. The conversation id is never echoed
back — the client generated it. `parseSSE` already passes unknown event
names through, so an older frontend against a newer backend simply
ignores the event.

### 3.6 Frontend

`frontend/lib/conversation.ts` (new): `getOrCreateConversationId()` reading
and writing `sessionStorage`, and a `startNewConversation()` that clears
it.

`frontend/lib/api.ts`'s `askStream` gains a `conversationId` parameter,
sent as `conversation_id` in the POST body.

`frontend/lib/answer.ts` gains a `resolved` case in `reduceAnswer` that
records `standaloneQuestion: string | null` on `AnswerState` (initial
`null`); the `default` branch already ignores any event it doesn't know,
so no other reducer change is needed. `frontend/lib/types.ts` documents
the event's payload.

The `/ask` page changes from rendering one active answer to rendering a
list of completed turns (question + streamed answer + citations, using the
existing components unchanged per-turn) with the live streaming turn
appended at the bottom, plus a "New conversation" button that calls
`startNewConversation()` and clears the local turn list. Each turn whose
stream carried a `resolved` event also renders the standalone question as
a caption under what the user typed (e.g. *Searched for: "What was Apple's
revenue in fiscal 2023?"*), so a wrong pronoun or period resolution shows
at a glance rather than only in the stored row. This is the one piece of
this spec that is a real UI restructure rather than an additive change —
everything else in the frontend (the filing viewer, citation chips,
highlight-on-click) is reused exactly as-is per turn.

## 4. Eval harness

`golden.yaml` is single-turn only, and building a multi-turn golden format
is its own scoping effort (v2 candidate, not folded into this spec).
**This feature ships without an automated eval gate** — acceptance is a
Playwright e2e spec covering one concrete multi-turn script (e.g. "What was
Apple's revenue in fiscal 2024?" then "and in fiscal 2023?") asserting the
second answer resolves to the right period **and that the second turn
renders the standalone-question caption** (proof the `resolved` event made
it end to end), plus manual verification. This
mirrors the precedent already set for multi-filing rendering: "the golden
set cannot measure multi-filing behaviour... verified by the e2e spec and
by hand, not by the evals" (`design.md`'s current-state log) — the same
honest limitation applies here for the same reason.

## 5. Rollout

Ship behind nothing special — `conversation_id` is optional and
`answer_stream`'s existing single-shot callers (evals, any direct caller
that doesn't pass one) are completely unaffected, since `history` is `[]`
whenever no id is given. Land the backend (migration + module + wiring)
and the frontend (threaded UI) together, since a backend-only version has
no way to be exercised by a real user and a frontend-only version has
nothing to call.

## 6. Risks

| risk | mitigation |
| --- | --- |
| one more sequential Haiku call on every turn after the first (compounds with spec 1's per-target rewrite and the existing detectors — design.md §6 already tracks this as "up to ~5 sequential calls," and this pushes the ceiling higher on long conversations) | only fires when `history` is non-empty (never on turn 1); capped history window (3 turns) bounds prompt growth as conversations get long |
| a conversation growing unbounded in storage with no cleanup | out of scope for v1 given no auth/quota exists to attach a retention policy to; note as a deferred concern rather than solving prematurely |
| the standalone-question rewrite changes what's being asked in a way that could be wrong (e.g., resolves "it" to the wrong prior subject) | the `/ask` UI shows the rewritten question as a caption on the turn whenever it differs from what the user typed (§3.5, §3.6), so a wrong resolution is visible immediately, not just in stored data; `standalone_question` is also persisted next to `question` for after-the-fact debugging |
| reversing a locked `design.md` §2 decision | the row exists in §14's own backlog already (`conversation history` is explicitly named); this spec is exercising already-planned scope, not an ad hoc reversal — `design.md` §2's table row must still be edited to reflect it, as part of implementation, not left contradicting the shipped feature |

## 7. Deferred

- Multi-turn golden-set eval format — needed before this feature's quality
  can be measured the way single-turn retrieval already is.
- Cross-device / persistent-login conversation history — would require the
  auth this project has never had; out of scope, not merely deferred.
- Conversation retention/cleanup policy.
- Editing or deleting past turns.
