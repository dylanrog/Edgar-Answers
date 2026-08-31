from __future__ import annotations

from dataclasses import dataclass

import psycopg


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
