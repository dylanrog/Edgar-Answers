-- One row per user turn in an anonymous, per-browser conversation
-- (spec 2026-08-30-conversation-memory-design.md §3.2). `conversation_id`
-- is a client-generated grouping key, not an account. No parent table:
-- nothing enumerates conversations.
CREATE TABLE conversation_turns (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_index INT NOT NULL,
    question TEXT NOT NULL,             -- what the user actually typed
    standalone_question TEXT NOT NULL,  -- the rewritten form used for retrieval
    answer_text TEXT NOT NULL,          -- the full streamed answer, reassembled
    tickers TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, turn_index)
);

CREATE INDEX conversation_turns_conversation_id_idx
    ON conversation_turns (conversation_id, turn_index);
