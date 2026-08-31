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
