import os

import psycopg
import pytest

from api.conversation import (
    Turn,
    build_resolution_prompt,
    load_recent_turns,
    parse_resolved_question,
    save_turn,
)
from pipeline import db


@pytest.fixture()
def conn():
    connection = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    db.migrate(connection)
    with connection.cursor() as cur:
        cur.execute("DELETE FROM conversation_turns")
    connection.commit()
    yield connection
    connection.close()


@pytest.mark.db
def test_save_then_load_round_trips_a_turn(conn):
    save_turn(
        conn,
        "c1",
        question="What was Apple's revenue?",
        standalone_question="What was Apple's revenue in fiscal 2024?",
        answer_text="It was 391 billion dollars.",
        tickers=["AAPL"],
    )
    assert load_recent_turns(conn, "c1") == [
        Turn(
            "What was Apple's revenue?",
            "What was Apple's revenue in fiscal 2024?",
            "It was 391 billion dollars.",
            ["AAPL"],
        )
    ]


@pytest.mark.db
def test_turn_index_autoincrements_within_a_conversation(conn):
    for i in range(3):
        save_turn(
            conn, "c1", question=f"q{i}", standalone_question=f"q{i}",
            answer_text=f"a{i}", tickers=[],
        )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT turn_index FROM conversation_turns"
            " WHERE conversation_id = 'c1' ORDER BY turn_index"
        )
        assert [row[0] for row in cur.fetchall()] == [0, 1, 2]


@pytest.mark.db
def test_turn_index_is_independent_across_conversations(conn):
    save_turn(conn, "c1", question="q", standalone_question="q", answer_text="a", tickers=[])
    save_turn(conn, "c2", question="q", standalone_question="q", answer_text="a", tickers=[])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT conversation_id, turn_index FROM conversation_turns"
            " ORDER BY conversation_id"
        )
        assert cur.fetchall() == [("c1", 0), ("c2", 0)]


@pytest.mark.db
def test_load_recent_turns_caps_at_limit_and_returns_oldest_first(conn):
    for i in range(5):
        save_turn(
            conn, "c1", question=f"q{i}", standalone_question=f"q{i}",
            answer_text=f"a{i}", tickers=[],
        )
    assert [t.question for t in load_recent_turns(conn, "c1", limit=3)] == ["q2", "q3", "q4"]


@pytest.mark.db
def test_save_past_the_load_window_keeps_incrementing_turn_index(conn):
    # load_recent_turns caps at 3, but turn_index must keep climbing or the
    # UNIQUE (conversation_id, turn_index) constraint collides on turn 4.
    for i in range(5):
        save_turn(
            conn, "c1", question=f"q{i}", standalone_question=f"q{i}",
            answer_text=f"a{i}", tickers=[],
        )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(turn_index) FROM conversation_turns WHERE conversation_id = 'c1'"
        )
        assert cur.fetchone()[0] == 4


@pytest.mark.db
def test_load_recent_turns_for_an_unknown_conversation_is_empty(conn):
    assert load_recent_turns(conn, "never-seen") == []


FALLBACK = "and in fiscal 2023?"


def test_parses_bare_json():
    assert (
        parse_resolved_question('{"question": "What was the revenue for fiscal 2023?"}', FALLBACK)
        == "What was the revenue for fiscal 2023?"
    )


def test_parses_fenced_json():
    assert parse_resolved_question('```json\n{"question": "X"}\n```', FALLBACK) == "X"


def test_malformed_json_falls_back():
    assert parse_resolved_question('{"question": [oops}', FALLBACK) == FALLBACK


def test_no_json_object_falls_back():
    assert parse_resolved_question("I am not sure what you mean.", FALLBACK) == FALLBACK


def test_missing_question_field_falls_back():
    assert parse_resolved_question('{"other": "x"}', FALLBACK) == FALLBACK


def test_non_string_question_field_falls_back():
    assert parse_resolved_question('{"question": 3}', FALLBACK) == FALLBACK


def test_blank_question_falls_back():
    assert parse_resolved_question('{"question": "   "}', FALLBACK) == FALLBACK


def test_question_is_stripped():
    assert parse_resolved_question('{"question": "  X  "}', FALLBACK) == "X"


def test_resolution_prompt_carries_history_the_followup_and_prior_tickers():
    history = [
        Turn(
            "What was Apple's fiscal 2024 revenue?",
            "What was Apple's fiscal 2024 revenue?",
            "Apple's total net sales were 391 billion dollars.",
            ["AAPL"],
        )
    ]
    prompt = build_resolution_prompt("and in fiscal 2023?", history)
    assert "What was Apple's fiscal 2024 revenue?" in prompt
    assert "and in fiscal 2023?" in prompt
    assert "AAPL" in prompt


def test_resolution_prompt_truncates_a_long_prior_answer():
    history = [Turn("q", "q", "x" * 5000, [])]
    prompt = build_resolution_prompt("follow-up", history)
    assert "x" * 5000 not in prompt
    assert "…" in prompt  # the ellipsis marking the cut


def _age(conn, conversation_id: str, days: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE conversation_turns SET created_at = now() - make_interval(days => %s)"
            " WHERE conversation_id = %s",
            (days, conversation_id),
        )
    conn.commit()


def _turns(conn, conversation_id: str) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT turn_index FROM conversation_turns WHERE conversation_id = %s"
            " ORDER BY turn_index",
            (conversation_id,),
        )
        return [row[0] for row in cur.fetchall()]


def _save(conn, conversation_id: str) -> None:
    save_turn(
        conn, conversation_id, question="q", standalone_question="q", answer_text="a", tickers=[]
    )


@pytest.mark.db
def test_save_prunes_conversations_idle_past_retention(conn):
    _save(conn, "stale")
    _save(conn, "stale")
    _age(conn, "stale", days=8)
    _save(conn, "fresh")
    assert _turns(conn, "stale") == []
    assert _turns(conn, "fresh") == [0]


@pytest.mark.db
def test_a_conversation_with_a_recent_turn_is_kept_whole(conn):
    _save(conn, "c1")
    _save(conn, "c1")
    with conn.cursor() as cur:  # its first turn is old, its latest is recent
        cur.execute(
            "UPDATE conversation_turns SET created_at = now() - interval '30 days'"
            " WHERE conversation_id = 'c1' AND turn_index = 0"
        )
    conn.commit()
    _save(conn, "other")
    assert _turns(conn, "c1") == [0, 1]


@pytest.mark.db
def test_resuming_a_pruned_conversation_restarts_cleanly(conn):
    for _ in range(3):
        _save(conn, "c1")
    _age(conn, "c1", days=8)
    _save(conn, "c1")
    assert _turns(conn, "c1") == [0]
