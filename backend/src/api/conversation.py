from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

import psycopg

from .generate import MODEL


@dataclass(frozen=True)
class Turn:
    question: str
    standalone_question: str
    answer_text: str
    tickers: list[str]


def load_recent_turns(
    conn: psycopg.Connection, conversation_id: str, *, limit: int = 3
) -> list[Turn]:
    """The last `limit` turns of a conversation, oldest-first (prompt order)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT question, standalone_question, answer_text, tickers"
            " FROM conversation_turns WHERE conversation_id = %s"
            " ORDER BY turn_index DESC LIMIT %s",
            (conversation_id, limit),
        )
        rows = cur.fetchall()
    return [Turn(*row) for row in reversed(rows)]


def save_turn(
    conn: psycopg.Connection,
    conversation_id: str,
    *,
    question: str,
    standalone_question: str,
    answer_text: str,
    tickers: list[str],
) -> None:
    """Append a turn. `turn_index` is the count of existing rows for this
    conversation_id (0 for the first turn) -- never passed by the caller.
    `load_recent_turns` returns at most 3 turns, so a caller deriving the
    index from `len(history)` would repeat 0-2 forever and collide with the
    UNIQUE constraint once a conversation passed 3 turns."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM conversation_turns WHERE conversation_id = %s",
            (conversation_id,),
        )
        turn_index = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO conversation_turns"
            " (conversation_id, turn_index, question, standalone_question,"
            "  answer_text, tickers)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                conversation_id,
                turn_index,
                question,
                standalone_question,
                answer_text,
                tickers,
            ),
        )
    conn.commit()


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_ANSWER_PREVIEW_CHARS = 400


class ConversationRewriter(Protocol):
    """The LLM, narrowed to the one thing conversation memory needs."""

    def resolve(self, question: str, history: list[Turn]) -> str:
        """Return a standalone version of `question`, given the prior turns."""
        ...


def parse_resolved_question(raw: str, fallback: str) -> str:
    """Defensive parse of a rewriter's raw response into a standalone question.

    Same contract as the rest of the detection layer: malformed JSON, no
    JSON object, a missing/non-string `question` field, or a blank question
    all degrade to `fallback` (the follow-up exactly as typed) -- a
    resolution failure must never make the question less answerable than not
    resolving it.
    """
    text = _FENCE.sub("", raw.strip())
    match = _OBJECT.search(text)
    if match is None:
        return fallback
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return fallback
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        return fallback
    return question.strip()


RESOLUTION_SYSTEM_PROMPT = (
    """You rewrite a follow-up question into a standalone question, using the
conversation so far.

You will be given the prior turns (each as the user's question and a short
preview of the answer, sometimes annotated with the companies it discussed)
and a new follow-up question. If the follow-up depends on the prior turns --
a pronoun ("it", "they", "that"), an implied company or time period, an
ellipsis ("and last year?"), or a contrast ("what about Microsoft
instead") -- rewrite it to be fully self-contained. If the follow-up
already stands on its own, return it unchanged.

Respond with strict JSON and nothing else:

{"question": "What was Apple's revenue in fiscal 2023?"}

Rules:
1. Preserve the user's intent exactly. Do not add a fact, number, company,
   or period that is not already in the conversation or the follow-up.
2. Only use company names or periods that appear in the prior turns or the
   follow-up.
3. If you cannot tell what the follow-up refers to, return it unchanged.
"""
)


def build_resolution_prompt(question: str, history: list[Turn]) -> str:
    blocks = []
    for i, turn in enumerate(history, start=1):
        answer = turn.answer_text[:_ANSWER_PREVIEW_CHARS]
        if len(turn.answer_text) > _ANSWER_PREVIEW_CHARS:
            answer += "…"
        block = f"Turn {i}:\nQ: {turn.question}\nA: {answer}"
        if turn.tickers:
            block += f"\n(companies discussed: {', '.join(turn.tickers)})"
        blocks.append(block)
    joined = "\n\n".join(blocks)
    return f"Conversation so far:\n{joined}\n\nFollow-up: {question}"


@dataclass
class AnthropicConversationRewriter:
    model: str = MODEL
    max_tokens: int = 256
    api_key: str | None = None

    def resolve(self, question: str, history: list[Turn]) -> str:
        import anthropic

        client = anthropic.Anthropic(
            api_key=self.api_key or os.environ["ANTHROPIC_API_KEY"]
        )
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            system=RESOLUTION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": build_resolution_prompt(question, history),
                }
            ],
        )
        raw = "".join(block.text for block in message.content if block.type == "text")
        return parse_resolved_question(raw, question)
