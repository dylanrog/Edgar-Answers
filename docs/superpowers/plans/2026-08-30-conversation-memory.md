# Conversation Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user ask follow-up questions on `/ask` ("and in fiscal
2023?", "how does that compare to Amazon?") by rewriting each follow-up
into a standalone question — using the last few turns of an anonymous,
per-browser conversation — before it enters the existing retrieval
pipeline unchanged.

**Architecture:** A fourth detector, `ConversationRewriter`, joins the
`CompanyDetector`/`PeriodDetector`/`QueryRewriter` trio. A new
`conversation_turns` table (migration `003`) persists each turn. At the
top of `answer_stream`, prior turns are loaded and the follow-up is
rewritten into `standalone_question`; that string — not the raw
`question` — feeds `resolve_targets`, `retrieve_for_targets`, and
`build_user_message`. When the rewrite changes the text, one new SSE
event (`resolved`) carries it to the client so the `/ask` UI can show
what was actually searched. Nothing about citation verification,
highlighting, or the chunk/sentence data model changes. The `/ask` page
restructures from one active answer to a list of turns.

**Tech Stack:** Python 3.13, FastAPI, psycopg 3, pytest, `anthropic` SDK;
Next.js 16 (App Router) + React 19 + TypeScript, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-30-conversation-memory-design.md`

## Global Constraints

- **Python 3.13 only.** No version-matrix changes; CI runs a single 3.13 job.
- **`ruff==0.16.1` is pinned exactly.** `ruff check .` (from `backend/`)
  must stay green on every commit. Do not bump the pin.
- **The `anthropic` SDK is pinned `>=0.120.2,<1`** in `backend/pyproject.toml`.
  Do not loosen or change it.
- **`MODEL` (from `backend/src/api/generate.py`, currently
  `"claude-haiku-4-5"`) is the single source of truth for the model id.**
  Never hardcode a model string anywhere else.
- **Degrade-safe detector rule:** wrap **only** the `.resolve()` call in
  `try/except Exception` — never the `load_recent_turns` DB read around
  it. This project has twice shipped an over-broad `try` that swallowed a
  DB error as "no result"; keep the `try` to the single line that calls
  the rewriter. A rewriter failure degrades to `standalone_question = question`.
- **A turn is persisted only on the `done` path.** `save_turn` is called
  inside the `try`, just before the `done` event is yielded. A request
  that ends in an `error` event saves nothing.
- **FastAPI dependency overrides in tests must be zero-arg lambdas**
  (`lambda: StubX()`), never the bare class — a bare class with an
  optional constructor parameter 422s every `/ask` call. See the existing
  comment in `backend/tests/test_app.py`'s `stubbed_client` fixture; do
  not remove or shorten that comment.
- **Report full pytest / vitest / playwright output in every task report.**
  A summarized "N passed" line is not sufficient evidence.
- **`TEST_DATABASE_URL` must be set** for any `@pytest.mark.db` test to
  run; they auto-skip otherwise. A skip is **not** a pass — say so if you
  cannot run them.
- **Commit messages contain no AI attribution of any kind** — no
  Co-Authored-By, no Claude-Session trailer, no tool names.
- **Frontend: this is not the Next.js you know.** Before writing or
  changing any file under `frontend/app/` or `frontend/components/`, read
  the relevant guide in `frontend/node_modules/next/dist/docs/` and heed
  deprecation notices (per `frontend/AGENTS.md`).
- **Frontend commands run from `frontend/` in PowerShell** — `node` is not
  on the git-bash PATH. `npm test` (vitest), `npm run lint` (eslint),
  `npm run test:e2e` (Playwright).
- **vitest runs the `node` environment by default.** A spec that touches
  the DOM or `sessionStorage` opts in with a `/** @vitest-environment jsdom */`
  docblock as its first line (see `frontend/lib/__tests__/highlight.test.ts`).

---

### Task 1: Migration + turn storage

**Files:**
- Create: `backend/migrations/003_conversation_turns.sql`
- Create: `backend/src/api/conversation.py`
- Create: `backend/tests/test_conversation.py`

**Interfaces:**
- Consumes: `pipeline.db.migrate` (applies numbered SQL in filename order;
  already skips migrations recorded in `schema_migrations`).
- Produces:
  - `Turn` — `@dataclass(frozen=True)` with fields **in this exact order**:
    `question: str`, `standalone_question: str`, `answer_text: str`,
    `tickers: list[str]`.
  - `load_recent_turns(conn: psycopg.Connection, conversation_id: str, *,
    limit: int = 3) -> list[Turn]` — the last `limit` turns, returned
    **oldest-first**.
  - `save_turn(conn: psycopg.Connection, conversation_id: str, *,
    question: str, standalone_question: str, answer_text: str,
    tickers: list[str]) -> None` — appends a turn; computes `turn_index`
    internally; commits.
  Task 2 adds the rewriter to this same module. Task 3 imports `Turn`,
  `load_recent_turns`, `save_turn`.

- [ ] **Step 1: Write the migration**

Create `backend/migrations/003_conversation_turns.sql` (match the
comment-then-SQL style of `001_init.sql` / `002_sentence_table_id.sql`):

```sql
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
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_conversation.py`:

```python
import os

import psycopg
import pytest

from api.conversation import Turn, load_recent_turns, save_turn
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run (from `backend/`, `TEST_DATABASE_URL` set):
`pytest tests/test_conversation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.conversation'`.

- [ ] **Step 4: Write `backend/src/api/conversation.py`**

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_conversation.py -v`
Expected: PASS, all 6 tests. Paste the full `-v` output.

- [ ] **Step 6: Lint**

Run: `ruff check .` (from `backend/`)
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add backend/migrations/003_conversation_turns.sql backend/src/api/conversation.py backend/tests/test_conversation.py
git commit -m "feat: add conversation_turns storage"
```

---

### Task 2: `ConversationRewriter`

**Files:**
- Modify: `backend/src/api/conversation.py`
- Modify: `backend/tests/test_conversation.py`

**Interfaces:**
- Consumes: `MODEL` from `backend/src/api/generate.py`; `Turn` from Task 1.
- Produces:
  - `ConversationRewriter` — `Protocol` with
    `resolve(self, question: str, history: list[Turn]) -> str`.
  - `parse_resolved_question(raw: str, fallback: str) -> str` — defensive
    parse; never raises.
  - `build_resolution_prompt(question: str, history: list[Turn]) -> str`.
  - `RESOLUTION_SYSTEM_PROMPT: str`.
  - `AnthropicConversationRewriter` — `@dataclass` with `model: str = MODEL`,
    `max_tokens: int = 256`, `api_key: str | None = None`, and
    `.resolve(question, history) -> str`.
  Task 3 imports `ConversationRewriter`. Task 4 imports
  `AnthropicConversationRewriter`.

Read `backend/src/api/rewrite.py` first — this task mirrors its shape
(Protocol + defensive parse + `Anthropic*` dataclass) line for line.

- [ ] **Step 1: Write the failing tests**

First, widen the existing import at the **top** of
`backend/tests/test_conversation.py` (do not add a second import block
mid-file — ruff's `E402` forbids it):

```python
from api.conversation import (
    Turn,
    build_resolution_prompt,
    load_recent_turns,
    parse_resolved_question,
    save_turn,
)
```

Then append these tests to the end of the file:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_conversation.py -v`
Expected: FAIL on `ImportError: cannot import name 'build_resolution_prompt'`.

- [ ] **Step 3: Extend `backend/src/api/conversation.py`**

Replace the module's import block (from Task 1 it is just `from __future__`,
`dataclass`, `psycopg`) with the full set — ruff `I` will reject any other
ordering:

```python
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

import psycopg

from .generate import MODEL
```

Append to the module (after `save_turn`):

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_conversation.py -v`
Expected: PASS, all tests (6 from Task 1 + 10 new). Paste the full output.

- [ ] **Step 5: Lint**

Run: `ruff check .`
Expected: no errors. (If ruff flags the `import` block order in the test
file, move the second `from api.conversation import ...` up next to the
first and drop the `# noqa: E402`.)

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/conversation.py backend/tests/test_conversation.py
git commit -m "feat: add the conversation follow-up rewriter"
```

---

### Task 3: Wire conversation memory into `answer_stream`

**Files:**
- Modify: `backend/src/api/answer.py`
- Modify: `backend/tests/fakes.py`
- Modify: `backend/tests/test_answer.py`

**Interfaces:**
- Consumes: `ConversationRewriter`, `Turn`, `load_recent_turns`,
  `save_turn` from `backend/src/api/conversation.py` (Tasks 1-2).
- Produces:
  - `answer_stream(..., conversation_id: str | None = None,
    conversation_rewriter: ConversationRewriter | None = None)` — two new
    keyword-only params after `query_rewriter`.
  - A new SSE event: `AnswerEvent("resolved", {"standalone_question": str})`,
    yielded once before the first `token`, **only when the rewrite changed
    the text**.
  - `StubConversationRewriter` in `backend/tests/fakes.py`.
  Task 4 threads `conversation_id` / `conversation_rewriter` through `/ask`.

Read `backend/src/api/answer.py` and `backend/tests/test_answer.py` in
full before starting.

- [ ] **Step 1: Add `StubConversationRewriter` to `backend/tests/fakes.py`**

Append, matching `StubQueryRewriter`'s shape immediately above it:

```python
class StubConversationRewriter:
    """Returns a canned standalone question per follow-up text, for testing
    consumers of ConversationRewriter without hitting the live API. Unknown
    follow-ups echo back unchanged, matching a no-op rewrite."""

    def __init__(self, answers: dict[str, str] | None = None):
        self.answers = answers or {}
        self.calls: list[tuple[str, tuple]] = []

    def resolve(self, question, history):
        self.calls.append((question, tuple(history)))
        return self.answers.get(question, question)
```

- [ ] **Step 2: Write the failing tests in `backend/tests/test_answer.py`**

First, add `conversation_turns` cleanup to the `seeded_conn` fixture so a
saved turn from one test cannot leak into another. In the `with
conn.cursor() as cur:` block that does the `DELETE`s, add this line
**first** (it has no foreign keys, so order is free):

```python
        cur.execute("DELETE FROM conversation_turns")
```

Then update the imports at the top of the file:

```python
from tests.fakes import (
    FakeEmbedder,
    StubCompanyDetector,
    StubConversationRewriter,
    StubGenerator,
)

from api.answer import answer_stream
from api.conversation import load_recent_turns, save_turn
```

Add this helper next to `collect`:

```python
def collect_conv(conn, *, conversation_id, rewriter, question, response):
    generator = StubGenerator(response)
    events = list(
        answer_stream(
            conn,
            FakeEmbedder(),
            generator,
            StubCompanyDetector(),
            question,
            conversation_id=conversation_id,
            conversation_rewriter=rewriter,
        )
    )
    return events, generator
```

Append these tests:

```python
@pytest.mark.db
def test_without_a_conversation_id_no_resolved_event_and_nothing_saved(seeded_conn):
    quote = "Total net sales were 391.0 billion dollars"
    events, _ = collect(seeded_conn, response_with(quote, chunk_id_of(seeded_conn)))
    assert "resolved" not in [e.name for e in events]
    with seeded_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM conversation_turns")
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_first_turn_saves_but_emits_no_resolved_event(seeded_conn):
    quote = "Total net sales were 391.0 billion dollars"
    events, _ = collect_conv(
        seeded_conn,
        conversation_id="conv-1",
        rewriter=StubConversationRewriter(),
        question="What were total net sales?",
        response=response_with(quote, chunk_id_of(seeded_conn)),
    )
    assert "resolved" not in [e.name for e in events]
    turns = load_recent_turns(seeded_conn, "conv-1")
    assert len(turns) == 1
    assert turns[0].question == "What were total net sales?"
    assert turns[0].standalone_question == "What were total net sales?"


@pytest.mark.db
def test_followup_is_rewritten_emitted_and_used_for_retrieval_and_saved(seeded_conn):
    save_turn(
        seeded_conn,
        "conv-2",
        question="What were fiscal 2024 net sales?",
        standalone_question="What were fiscal 2024 net sales?",
        answer_text="They were 391 billion dollars.",
        tickers=["TSTE"],
    )
    rewriter = StubConversationRewriter(
        {"and services?": "What was Services revenue?"}
    )
    quote = "Services revenue reached an all-time record."
    events, _ = collect_conv(
        seeded_conn,
        conversation_id="conv-2",
        rewriter=rewriter,
        question="and services?",
        response=response_with(quote, chunk_id_of(seeded_conn)),
    )
    resolved = [e for e in events if e.name == "resolved"]
    assert resolved and resolved[0].data == {
        "standalone_question": "What was Services revenue?"
    }
    assert rewriter.calls and rewriter.calls[0][0] == "and services?"
    turns = load_recent_turns(seeded_conn, "conv-2")
    assert turns[-1].question == "and services?"
    assert turns[-1].standalone_question == "What was Services revenue?"


@pytest.mark.db
def test_rewriter_returning_the_same_text_emits_no_resolved_event(seeded_conn):
    save_turn(
        seeded_conn, "conv-3", question="q0", standalone_question="q0",
        answer_text="a0", tickers=[],
    )
    quote = "Total net sales were 391.0 billion dollars"
    events, _ = collect_conv(
        seeded_conn,
        conversation_id="conv-3",
        rewriter=StubConversationRewriter(),  # echoes the follow-up unchanged
        question="What were total net sales?",
        response=response_with(quote, chunk_id_of(seeded_conn)),
    )
    assert "resolved" not in [e.name for e in events]


@pytest.mark.db
def test_rewriter_failure_degrades_to_the_raw_followup(seeded_conn):
    save_turn(
        seeded_conn, "conv-4", question="q0", standalone_question="q0",
        answer_text="a0", tickers=[],
    )

    class BoomRewriter:
        def resolve(self, question, history):
            raise RuntimeError("rate limited")

    quote = "Total net sales were 391.0 billion dollars"
    events, _ = collect_conv(
        seeded_conn,
        conversation_id="conv-4",
        rewriter=BoomRewriter(),
        question="What were total net sales?",
        response=response_with(quote, chunk_id_of(seeded_conn)),
    )
    assert "resolved" not in [e.name for e in events]
    assert [e.name for e in events][-1] == "done"
    assert load_recent_turns(seeded_conn, "conv-4")[-1].standalone_question == (
        "What were total net sales?"
    )


@pytest.mark.db
def test_no_turn_is_saved_when_generation_errors(seeded_conn):
    class Boom:
        def stream(self, system, user):
            raise RuntimeError("upstream is down")
            yield  # pragma: no cover

    events = list(
        answer_stream(
            seeded_conn,
            FakeEmbedder(),
            Boom(),
            StubCompanyDetector(),
            "What were net sales?",
            conversation_id="conv-5",
            conversation_rewriter=StubConversationRewriter(),
        )
    )
    assert events[-1].name == "error"
    assert load_recent_turns(seeded_conn, "conv-5") == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_answer.py -v`
Expected: FAIL — `answer_stream()` got an unexpected keyword argument
`conversation_id`, plus `ImportError` for `StubConversationRewriter`.

- [ ] **Step 4: Modify `backend/src/api/answer.py`**

Add imports (top of file):

```python
import logging
```

and, with the other `from .` imports:

```python
from .conversation import ConversationRewriter, load_recent_turns, save_turn
```

After the imports, add a module logger (mirrors `api/targets.py`):

```python
logger = logging.getLogger(__name__)
```

Replace the `answer_stream` signature and the top of its `try` block —
everything from `def answer_stream(` down to and including the
`user_message = build_user_message(...)` line — with:

```python
def answer_stream(
    conn: psycopg.Connection,
    embedder,
    generator: Generator,
    company_detector: CompanyDetector,
    question: str,
    *,
    tickers: list[str] | None = None,
    form_type: str | None = None,
    k_final: int = 8,
    period_detector: PeriodDetector | None = None,
    query_rewriter: QueryRewriter | None = None,
    conversation_id: str | None = None,
    conversation_rewriter: ConversationRewriter | None = None,
) -> Iterator[AnswerEvent]:
    """The query path (design §6): [resolve follow-up] -> resolve targets ->
    retrieve -> generate -> verify -> stream."""
    try:
        history = load_recent_turns(conn, conversation_id) if conversation_id else []
        standalone_question = question
        if history and conversation_rewriter is not None:
            try:
                standalone_question = conversation_rewriter.resolve(question, history)
            except Exception as exc:  # noqa: BLE001 -- degrades to the raw follow-up
                logger.warning(
                    "conversation_rewriter.resolve failed, using the raw question: %s",
                    exc,
                )
        if standalone_question != question:
            yield AnswerEvent("resolved", {"standalone_question": standalone_question})

        targets = resolve_targets(
            conn,
            standalone_question,
            explicit_tickers=tickers,
            company_detector=company_detector,
            period_detector=period_detector,
            query_rewriter=query_rewriter,
        )
        chunks = retrieve_for_targets(
            conn, embedder, standalone_question, targets, k_final=k_final, form_type=form_type
        )
        user_message = build_user_message(standalone_question, chunks)
```

In the retry loop, accumulate the streamed prose. Change this block:

```python
        splitter = AnswerSplitter()
        citations = None
        for attempt in range(2):
            splitter = AnswerSplitter()
            for delta in generator.stream(SYSTEM_PROMPT, user_message):
                text = splitter.feed(delta)
                if text and attempt == 0:
                    yield AnswerEvent("token", {"text": text})
            tail = splitter.finish()
            if tail and attempt == 0:
                yield AnswerEvent("token", {"text": tail})
            citations = parse_citations(splitter.raw)
            if citations is not None:
                break
```

to:

```python
        answer_parts: list[str] = []
        splitter = AnswerSplitter()
        citations = None
        for attempt in range(2):
            splitter = AnswerSplitter()
            for delta in generator.stream(SYSTEM_PROMPT, user_message):
                text = splitter.feed(delta)
                if text and attempt == 0:
                    answer_parts.append(text)
                    yield AnswerEvent("token", {"text": text})
            tail = splitter.finish()
            if tail and attempt == 0:
                answer_parts.append(tail)
                yield AnswerEvent("token", {"text": tail})
            citations = parse_citations(splitter.raw)
            if citations is not None:
                break
```

Finally, persist the turn immediately before the `done` event. Insert
this directly above the `yield AnswerEvent("done", {...})` line:

```python
        if conversation_id is not None:
            save_turn(
                conn,
                conversation_id,
                question=question,
                standalone_question=standalone_question,
                answer_text="".join(answer_parts),
                tickers=[target.ticker for target in targets],
            )

```

(Everything else — the verification loop, the `citation` events, the
`done` payload, the `except` handler — is unchanged.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_answer.py -v`
Expected: PASS, existing + 6 new. Paste the full output.

- [ ] **Step 6: Run the full backend suite**

Run: `pytest -v` (from `backend/`, `TEST_DATABASE_URL` set)
Expected: PASS, no regressions (`test_app.py` still green — Task 4 has not
touched it yet, and `answer_stream`'s new params are optional). Paste the
full output.

- [ ] **Step 7: Lint**

Run: `ruff check .`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add backend/src/api/answer.py backend/tests/fakes.py backend/tests/test_answer.py
git commit -m "feat: rewrite conversation follow-ups before retrieval"
```

---

### Task 4: Expose `conversation_id` on `POST /ask`

**Files:**
- Modify: `backend/src/api/app.py`
- Modify: `backend/tests/test_app.py`

**Interfaces:**
- Consumes: `AnthropicConversationRewriter` / `ConversationRewriter` from
  `backend/src/api/conversation.py` (Task 2); `answer_stream(...,
  conversation_id=..., conversation_rewriter=...)` (Task 3).
- Produces: `AskRequest.conversation_id: str | None`;
  `app.get_conversation_rewriter()` DI provider.

- [ ] **Step 1: Modify `backend/src/api/app.py`**

Add the import next to `from .rewrite import ...`:

```python
from .conversation import AnthropicConversationRewriter, ConversationRewriter
```

Add `conversation_id` to `AskRequest` (a session/thread key, not a
retrieval filter — so a top-level field, sibling to `filters`; capped so a
garbage megastring cannot be written):

```python
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    filters: Filters = Field(default_factory=Filters)
    conversation_id: str | None = Field(default=None, max_length=200)

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()
```

Add the DI provider next to `get_query_rewriter`:

```python
def get_conversation_rewriter() -> ConversationRewriter:
    return AnthropicConversationRewriter()
```

Update `ask()` — add the dependency parameter and pass both new values
through to `answer_stream`:

```python
@app.post("/ask")
def ask(
    request: AskRequest,
    embedder: Embedder = Depends(get_embedder),
    generator: Generator = Depends(get_generator),
    company_detector: CompanyDetector = Depends(get_company_detector),
    period_detector: PeriodDetector = Depends(get_period_detector),
    query_rewriter: QueryRewriter = Depends(get_query_rewriter),
    conversation_rewriter: ConversationRewriter = Depends(get_conversation_rewriter),
) -> StreamingResponse:
    # The plural filter wins; a lone legacy singular `ticker` is wrapped into
    # a one-element list so existing single-ticker API callers keep working.
    tickers = request.filters.tickers or (
        [request.filters.ticker] if request.filters.ticker else None
    )

    def events() -> Iterator[str]:
        # A connection per request; pooling is a Phase 5 concern.
        with db.connect() as conn:
            for event in answer_stream(
                conn,
                embedder,
                generator,
                company_detector,
                request.question,
                tickers=tickers,
                form_type=request.filters.form_type,
                period_detector=period_detector,
                query_rewriter=query_rewriter,
                conversation_id=request.conversation_id,
                conversation_rewriter=conversation_rewriter,
            ):
                yield sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 2: Modify `backend/tests/test_app.py`**

Add `StubConversationRewriter` to the `from tests.fakes import (...)` block.

In the `stubbed_client` fixture, add this override alongside the other
detector overrides (the zero-arg-lambda comment above them already
explains why a bare class is unsafe — leave that comment intact):

```python
    app.dependency_overrides[app_module.get_conversation_rewriter] = (
        lambda: StubConversationRewriter()  # noqa: PLW0108
    )
```

Add two tests:

```python
def test_ask_rejects_an_overlong_conversation_id():
    with TestClient(app) as client:
        response = client.post(
            "/ask", json={"question": "hi", "conversation_id": "x" * 500}
        )
    assert response.status_code == 422


@pytest.mark.db
def test_ask_with_a_conversation_id_persists_the_turn(stubbed_client, seeded_conn):
    response = stubbed_client.post(
        "/ask",
        json={"question": "What were net sales?", "conversation_id": "c-http"},
    )
    assert response.status_code == 200
    list(parse_sse(response.text))  # drain the stream
    with seeded_conn.cursor() as cur:
        cur.execute(
            "SELECT question FROM conversation_turns WHERE conversation_id = 'c-http'"
        )
        assert cur.fetchone()[0] == "What were net sales?"
```

- [ ] **Step 3: Run the full backend suite**

Run: `pytest -v` (from `backend/`, `TEST_DATABASE_URL` set)
Expected: PASS, no regressions. Paste the full output.

- [ ] **Step 4: Lint**

Run: `ruff check .`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/app.py backend/tests/test_app.py
git commit -m "feat: accept conversation_id on POST /ask"
```

---

### Task 5: Frontend conversation-id storage

**Files:**
- Create: `frontend/lib/conversation.ts`
- Create: `frontend/lib/__tests__/conversation.test.ts`

**Interfaces:**
- Produces:
  - `getOrCreateConversationId(): string` — reads/writes `sessionStorage`;
    returns a stable id within a browser session.
  - `startNewConversation(): void` — clears the stored id so the next
    `getOrCreateConversationId()` returns a fresh one.
  Task 6 does not use these; Task 7 (the `/ask` page) does.

- [ ] **Step 1: Write the failing tests**

Create `frontend/lib/__tests__/conversation.test.ts`:

```ts
/**
 * @vitest-environment jsdom
 */
import { afterEach, expect, test } from "vitest";

import { getOrCreateConversationId, startNewConversation } from "../conversation";

afterEach(() => sessionStorage.clear());

test("the id is stable within a session", () => {
  expect(getOrCreateConversationId()).toBe(getOrCreateConversationId());
});

test("it survives a simulated reload (value already in sessionStorage)", () => {
  const first = getOrCreateConversationId();
  // A reload keeps sessionStorage but loses module state — mimic by reading raw.
  expect(sessionStorage.getItem("edgar-answers.conversation-id")).toBe(first);
});

test("startNewConversation forces a fresh id on the next call", () => {
  const first = getOrCreateConversationId();
  startNewConversation();
  expect(getOrCreateConversationId()).not.toBe(first);
});

test("the id looks like a UUID", () => {
  expect(getOrCreateConversationId()).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
  );
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`, PowerShell): `npm test -- conversation`
Expected: FAIL — cannot resolve `../conversation`.

- [ ] **Step 3: Write `frontend/lib/conversation.ts`**

```ts
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm test -- conversation`
Expected: PASS, all 4. Paste the full output. (If `crypto.randomUUID` is
undefined in the test runtime, the Node version is too old for this
project's Next 16 / React 19 baseline — stop and report; do not polyfill.)

- [ ] **Step 5: Lint**

Run: `npm run lint` (from `frontend/`)
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/conversation.ts frontend/lib/__tests__/conversation.test.ts
git commit -m "feat: per-session conversation id in sessionStorage"
```

---

### Task 6: Frontend client plumbing for `conversation_id` and the `resolved` event

**Files:**
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/lib/answer.ts`
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/__tests__/answer.test.ts`

**Interfaces:**
- Produces:
  - `askStream(question, filters?, conversationId?, signal?)` — new third
    param, sent as `conversation_id` in the POST body.
  - `AnswerState.standaloneQuestion: string | null` (initial `null`).
  - `reduceAnswer` handles `event.event === "resolved"` with
    `{ standalone_question: string }`.
  Task 7 consumes all three.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/lib/__tests__/answer.test.ts`:

```ts
test("a resolved event records the standalone question", () => {
  const state = feed([
    ["resolved", { standalone_question: "What was Apple revenue in fiscal 2023?" }],
    ["token", { text: "It was 383 billion." }],
  ]);
  expect(state.standaloneQuestion).toBe("What was Apple revenue in fiscal 2023?");
  expect(state.prose).toBe("It was 383 billion.");
});

test("standaloneQuestion starts null", () => {
  expect(initialAnswerState.standaloneQuestion).toBeNull();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npm test -- answer`
Expected: FAIL — `state.standaloneQuestion` is `undefined`, not the
asserted value / `null`.

- [ ] **Step 3: Modify `frontend/lib/answer.ts`**

Add the field to the type and the initial state, and a `resolved` case to
the reducer:

```ts
export type AnswerState = {
  prose: string;
  citations: Map<number, Citation>;
  status: "idle" | "streaming" | "done" | "error";
  /** Set only when the model's citation block never parsed (design §10). */
  notice: string | null;
  /** Set only when status is "error". */
  errorMessage: string | null;
  chunksRetrieved: number | null;
  /** The rewritten, self-contained question used for retrieval — set only
   *  when a follow-up was rewritten (design §6.4 `resolved` event). */
  standaloneQuestion: string | null;
};

export const initialAnswerState: AnswerState = {
  prose: "",
  citations: new Map(),
  status: "idle",
  notice: null,
  errorMessage: null,
  chunksRetrieved: null,
  standaloneQuestion: null,
};
```

In the `reduceAnswer` switch, add before `default:`:

```ts
    case "resolved": {
      const { standalone_question } = event.data as { standalone_question: string };
      return { ...state, standaloneQuestion: standalone_question };
    }
```

- [ ] **Step 4: Modify `frontend/lib/types.ts`**

Extend the `SSEEvent` doc comment so the event contract is documented in
one place:

```ts
/**
 * One decoded SSE frame from POST /ask. Event names:
 * - `token`     {"text": string}                       — answer deltas
 * - `resolved`  {"standalone_question": string}         — a rewritten follow-up,
 *                                                         emitted once before the
 *                                                         first token, only when
 *                                                         the rewrite changed the text
 * - `citation`  Citation
 * - `done`      {chunks_retrieved, citations_total, citations_verified, unverified_answer}
 * - `error`     {"message": string}
 */
export type SSEEvent = { event: string; data: unknown };
```

- [ ] **Step 5: Modify `frontend/lib/api.ts`**

Add the `conversationId` parameter (before `signal`, so the existing
two-arg call sites are unaffected) and include it in the body:

```ts
export async function* askStream(
  question: string,
  filters: AskFilters = {},
  conversationId?: string,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const response = await fetch(`${API_URL}/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, filters, conversation_id: conversationId }),
    signal,
  });
```

(`JSON.stringify` omits `conversation_id` entirely when `conversationId`
is `undefined`, and the backend field defaults to `None` — so a caller
that passes nothing behaves exactly as before.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `npm test` (from `frontend/`, the whole suite)
Expected: PASS — every spec, including the 4 from Task 5's
`conversation.test.ts` and the 2 new ones in `answer.test.ts`. Paste the
full output.

- [ ] **Step 7: Lint**

Run: `npm run lint`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/lib/api.ts frontend/lib/answer.ts frontend/lib/types.ts frontend/lib/__tests__/answer.test.ts
git commit -m "feat: send conversation_id and handle the resolved SSE event"
```

---

### Task 7: Restructure `/ask` into a conversation thread

**Files:**
- Create: `frontend/components/conversation-turn.tsx`
- Modify: `frontend/app/ask/page.tsx`

**Interfaces:**
- Consumes: `getOrCreateConversationId`, `startNewConversation` (Task 5);
  `askStream(..., conversationId)`, `AnswerState.standaloneQuestion`
  (Task 6); the existing `AnswerStream`, `SourcesPanel`, `groupSources`,
  `AskForm`, `FilingTabs`, `FilingViewer`, `openTab`/`closeTab` — all
  unchanged.
- Produces: nothing consumed by a later task except Task 8's e2e assertions.

Read `frontend/node_modules/next/dist/docs/` for the current App Router
client-component rules before editing `page.tsx`. The existing page is
already `"use client"`; keep it that way.

- [ ] **Step 1: Write `frontend/components/conversation-turn.tsx`**

One completed (or in-flight) turn: the user's question, the optional
"Searched for" caption, that turn's streamed answer, and that turn's
sources panel. The right-hand filing viewer is shared across turns and
stays in the page.

```tsx
"use client";

import { useMemo } from "react";

import { AnswerStream } from "@/components/answer-stream";
import { SourcesPanel } from "@/components/sources-panel";
import type { AnswerState } from "@/lib/answer";
import { groupSources } from "@/lib/sources";
import type { Citation } from "@/lib/types";

export function ConversationTurn({
  question,
  state,
  onSelect,
}: {
  question: string;
  state: AnswerState;
  onSelect: (citation: Citation) => void;
}) {
  const groups = useMemo(() => groupSources(state.citations), [state.citations]);

  return (
    <article className="mb-6 border-b border-slate-800 pb-5 last:border-b-0 last:pb-0">
      <p className="text-sm font-semibold text-slate-100">{question}</p>
      {state.standaloneQuestion && (
        <p className="mt-1 text-xs text-slate-500">
          Searched for: “{state.standaloneQuestion}”
        </p>
      )}
      <div className="mt-2">
        <AnswerStream state={state} onSelect={onSelect} />
      </div>
      <SourcesPanel groups={groups} onSelect={onSelect} />
    </article>
  );
}
```

- [ ] **Step 2: Rewrite `frontend/app/ask/page.tsx`**

The page now holds a list of turns instead of one answer. Each new
question appends a turn and streams into it; the filing viewer
(`tabs`/`sids`) is shared and is **not** reset between follow-ups — only
"New conversation" clears everything.

```tsx
"use client";

import { useMemo, useState } from "react";

import { AskForm } from "@/components/ask-form";
import { ConversationTurn } from "@/components/conversation-turn";
import { FilingTabs } from "@/components/filing-tabs";
import { FilingViewer } from "@/components/filing-viewer";
import { initialAnswerState, reduceAnswer } from "@/lib/answer";
import type { AnswerState } from "@/lib/answer";
import { askStream } from "@/lib/api";
import type { AskFilters } from "@/lib/api";
import { getOrCreateConversationId, startNewConversation } from "@/lib/conversation";
import { closeTab, initialTabState, openTab } from "@/lib/tabs";
import type { Citation } from "@/lib/types";

type TurnView = { question: string; state: AnswerState };

export default function AskPage() {
  const [turns, setTurns] = useState<TurnView[]>([]);
  const [tabs, setTabs] = useState(initialTabState);
  const [sids, setSids] = useState<Record<string, number[]>>({});

  const streaming = turns.at(-1)?.state.status === "streaming";

  // accession -> tab label, across every turn. The year disambiguates
  // same-company, same-form-type filings that would otherwise render
  // identically-labelled tabs.
  const labels = useMemo(() => {
    const out: Record<string, string> = {};
    for (const turn of turns) {
      for (const citation of turn.state.citations.values()) {
        if (citation.accession) {
          out[citation.accession] =
            `${citation.ticker} ${citation.form_type} ${citation.filing_date.slice(0, 4)}`;
        }
      }
    }
    return out;
  }, [turns]);

  function patchLastTurn(update: (state: AnswerState) => AnswerState) {
    setTurns((previous) => {
      if (previous.length === 0) return previous;
      const next = [...previous];
      const last = next[next.length - 1];
      next[next.length - 1] = { ...last, state: update(last.state) };
      return next;
    });
  }

  async function ask(question: string, filters: AskFilters) {
    const conversationId = getOrCreateConversationId();
    setTurns((previous) => [
      ...previous,
      { question, state: { ...initialAnswerState, status: "streaming" } },
    ]);
    try {
      for await (const event of askStream(question, filters, conversationId)) {
        patchLastTurn((state) => reduceAnswer(state, event));
      }
    } catch (error) {
      patchLastTurn((state) => ({
        ...state,
        status: "error",
        errorMessage:
          error instanceof Error ? error.message : "Could not reach the API.",
      }));
    }
  }

  function newConversation() {
    startNewConversation();
    setTurns([]);
    setTabs(initialTabState);
    setSids({});
  }

  function select(citation: Citation) {
    // Unverified and unattributable citations are inert by design (§6.3):
    // there is nothing trustworthy to scroll to.
    if (!citation.verified || citation.accession === "") return;
    setTabs((previous) => openTab(previous, citation.accession));
    setSids((previous) => ({ ...previous, [citation.accession]: citation.sids }));
  }

  return (
    <main className="grid h-screen grid-cols-[minmax(0,5fr)_minmax(0,7fr)] bg-slate-950 text-slate-200">
      <section className="overflow-y-auto border-r border-slate-800 p-5">
        <div className="mb-4 flex items-baseline justify-between">
          <h1 className="font-mono text-sm font-bold tracking-wide text-slate-100">
            EDGAR ANSWERS
          </h1>
          {turns.length > 0 && (
            <button
              type="button"
              onClick={newConversation}
              className="text-xs text-slate-500 underline hover:text-slate-300"
            >
              New conversation
            </button>
          )}
        </div>
        <AskForm disabled={streaming} onSubmit={ask} />
        {turns.length === 0 ? (
          <p className="text-slate-500">Ask a question about a filing.</p>
        ) : (
          turns.map((turn, index) => (
            <ConversationTurn
              key={index}
              question={turn.question}
              state={turn.state}
              onSelect={select}
            />
          ))
        )}
      </section>

      <section className="flex flex-col overflow-hidden">
        <FilingTabs
          tabs={tabs}
          labels={labels}
          onActivate={(accession) => setTabs((previous) => openTab(previous, accession))}
          onClose={(accession) => setTabs((previous) => closeTab(previous, accession))}
        />
        <div className="min-h-0 flex-1 bg-slate-950">
          <FilingViewer tabs={tabs} sids={sids} />
        </div>
      </section>
    </main>
  );
}
```

- [ ] **Step 3: Run the whole vitest suite**

Run: `npm test` (from `frontend/`)
Expected: PASS, no regressions (no unit spec imports `page.tsx`). Paste
the full output.

- [ ] **Step 4: Run the existing e2e specs to confirm no regression**

Run: `npm run test:e2e` (from `frontend/`)
Expected: PASS — `highlight.spec.ts` (2) and `multi-source.spec.ts` (1)
still pass. The single-question flow they exercise is unchanged: one turn
renders, its chips and sources panel behave as before, and the shared
viewer still opens on citation click. Paste the full output. If
`multi-source.spec.ts` fails on the `Sources · 2 filings · 2 citations`
text, check that `SourcesPanel` is rendered once per turn inside
`ConversationTurn` and not accidentally dropped.

- [ ] **Step 5: Lint**

Run: `npm run lint`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/conversation-turn.tsx frontend/app/ask/page.tsx
git commit -m "feat: render /ask as a conversation thread"
```

---

### Task 8: Multi-turn e2e spec

**Files:**
- Create: `frontend/e2e/conversation.spec.ts`

**Interfaces:**
- Consumes: the running app from Tasks 5-7. All backend calls are stubbed
  with `page.route`, so this task needs no backend and no API key.
- Produces: the spec §4 acceptance test.

Read `frontend/e2e/multi-source.spec.ts` first — this spec follows its
`page.route` + SSE-string pattern exactly.

- [ ] **Step 1: Write `frontend/e2e/conversation.spec.ts`**

```ts
import { expect, test } from "@playwright/test";

const FY24 = "0000320193-24-000123";
const FY23 = "0000320193-23-000106";
const API = "http://localhost:8000";

// Keep the marker at the very end of the token text: AnswerStream splits
// "[1]" into its own <button>, so an assertion that straddles the marker
// ("...2024 [1].") would span two elements and not match with getByText.
const FIRST_SSE = [
  'event: token\ndata: {"text":"Total net sales in fiscal 2024 were 391 billion [1]"}\n\n',
  `event: citation\ndata: {"marker":1,"verified":true,"accession":"${FY24}",`,
  '"ticker":"AAPL","form_type":"10-K","filing_date":"2024-11-01",',
  '"sids":[1],"quote":"Total net sales 391,035"}\n\n',
  'event: done\ndata: {"chunks_retrieved":8,"citations_total":1,',
  '"citations_verified":1,"unverified_answer":false}\n\n',
].join("");

const FOLLOWUP_SSE = [
  'event: resolved\ndata: {"standalone_question":"What was the revenue for fiscal 2023?"}\n\n',
  'event: token\ndata: {"text":"Total net sales in fiscal 2023 were 383 billion [1]"}\n\n',
  `event: citation\ndata: {"marker":1,"verified":true,"accession":"${FY23}",`,
  '"ticker":"AAPL","form_type":"10-K","filing_date":"2023-11-03",',
  '"sids":[1],"quote":"Total net sales 383,285"}\n\n',
  'event: done\ndata: {"chunks_retrieved":8,"citations_total":1,',
  '"citations_verified":1,"unverified_answer":false}\n\n',
].join("");

function filing(accession: string, html: string, filed: string) {
  return {
    accession,
    viewer_html: html,
    ticker: "AAPL",
    form_type: "10-K",
    filing_date: filed,
    period_end: null,
  };
}

test("a follow-up streams a second turn and shows the rewritten query", async ({
  page,
}) => {
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        { cik: 320193, ticker: "AAPL", name: "Apple Inc.", filings: 13 },
      ]),
    }),
  );

  const conversationIds: Array<string | undefined> = [];
  await page.route(`${API}/ask`, (route) => {
    const body = route.request().postDataJSON() as {
      question: string;
      conversation_id?: string;
    };
    conversationIds.push(body.conversation_id);
    const isFollowup = /2023|and in/i.test(body.question);
    route.fulfill({
      contentType: "text/event-stream",
      body: isFollowup ? FOLLOWUP_SSE : FIRST_SSE,
    });
  });

  await page.route(`${API}/filings/${FY24}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(
        filing(FY24, '<p><span data-sid="1">Total net sales 391,035</span></p>', "2024-11-01"),
      ),
    }),
  );
  await page.route(`${API}/filings/${FY23}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(
        filing(FY23, '<p><span data-sid="1">Total net sales 383,285</span></p>', "2023-11-03"),
      ),
    }),
  );

  await page.goto("/ask");

  await page.getByLabel("Question").fill("What were Apple's total net sales in fiscal 2024?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("in fiscal 2024 were 391 billion")).toBeVisible();

  await page.getByLabel("Question").fill("and in fiscal 2023?");
  await page.getByRole("button", { name: "Ask" }).click();

  // The second turn shows the rewritten, self-contained question ...
  const caption = page.getByText(/^Searched for:/);
  await expect(caption).toContainText("What was the revenue for fiscal 2023?");
  // ... its own answer, and the first turn is still on the page.
  await expect(page.getByText("in fiscal 2023 were 383 billion")).toBeVisible();
  await expect(page.getByText("in fiscal 2024 were 391 billion")).toBeVisible();

  // Both /ask calls carried the same, non-empty conversation id.
  expect(conversationIds[0]).toBeTruthy();
  expect(conversationIds[1]).toBe(conversationIds[0]);

  // "New conversation" clears the thread.
  await page.getByRole("button", { name: "New conversation" }).click();
  await expect(page.getByText("in fiscal 2024 were 391 billion")).toHaveCount(0);
  await expect(page.getByText("Ask a question about a filing.")).toBeVisible();
});
```

- [ ] **Step 2: Run the e2e suite**

Run: `npm run test:e2e` (from `frontend/`)
Expected: PASS — the new spec plus the 3 existing e2e tests. Paste the
full output.

- [ ] **Step 3: Lint**

Run: `npm run lint`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/conversation.spec.ts
git commit -m "test: e2e multi-turn conversation on /ask"
```

---

### Task 9: Documentation

**Files:**
- Modify: `docs/design.md`
- Modify: `docs/superpowers/specs/2026-08-30-conversation-memory-design.md`

**Interfaces:** none — docs only.

- [ ] **Step 1: Update `docs/design.md` §2 (Scope decisions)**

Replace this row:

```
| Auth, chat history, threading | **Out** | Not what this project is for |
```

with:

```
| Auth, threading | **Out** | Not what this project is for |
| Chat history | **In** (2026-08-30) | Anonymous, per-browser conversation continuity — no accounts; see `docs/superpowers/specs/2026-08-30-conversation-memory-design.md` |
```

- [ ] **Step 2: Update `docs/design.md` §6 (Query path) latency note**

At the end of the paragraph that begins "Resolving targets adds latency
before retrieval even starts" (the one ending "...even when the question
names no company."), append:

```
A follow-up question in an ongoing conversation adds one more sequential
Haiku call before target resolution — the standalone-question rewrite. It
fires only when the conversation already has at least one stored turn, so
the first question of every conversation is unaffected.
```

- [ ] **Step 3: Update `docs/design.md` §6.4 (SSE events)**

Add a `resolved` line to the code block, directly under `token`:

```
resolved: {"standalone_question": "…"}            -- a rewritten follow-up;
                                                     emitted once before the first
                                                     token, only when the rewrite
                                                     changed the question
```

- [ ] **Step 4: Add a current-state bullet to `docs/design.md`**

In the dated "Current state" section, add a bullet (matching the style of
the existing ones):

```
- **Conversation memory shipped** (spec
  `docs/superpowers/specs/2026-08-30-conversation-memory-design.md`, plan
  `docs/superpowers/plans/2026-08-30-conversation-memory.md`): `/ask`
  accepts an optional `conversation_id`; a follow-up is rewritten into a
  standalone question (one Haiku call, last 3 turns) before it enters
  `resolve_targets` unchanged, and the rewrite is surfaced to the user via
  a new `resolved` SSE event and a "Searched for:" caption on the turn.
  New table `conversation_turns` (migration 003). The `/ask` page is now a
  thread of turns. **No automated eval gate** — the golden set is
  single-turn, so this is covered by `frontend/e2e/conversation.spec.ts`
  and manual verification, the same limitation already noted for
  multi-filing behaviour.
```

- [ ] **Step 5: Flip the spec status**

In `docs/superpowers/specs/2026-08-30-conversation-memory-design.md`,
change:

```
**Status:** proposed
```

to:

```
**Status:** accepted — plan docs/superpowers/plans/2026-08-30-conversation-memory.md
```

- [ ] **Step 6: Commit**

```bash
git add docs/design.md docs/superpowers/specs/2026-08-30-conversation-memory-design.md
git commit -m "docs: record conversation memory in the design spec"
```

---

## Final Verification (controller, after all tasks pass review)

1. **Full backend suite, clean:** from `backend/` with `TEST_DATABASE_URL`
   set, `pytest -v` — everything green, no skips among the `db` tests.
2. **Full frontend suite:** from `frontend/`, `npm test` and
   `npm run test:e2e` — all green, including the two pre-existing e2e
   specs.
3. **Lint both:** `ruff check .` (backend) and `npm run lint` (frontend).
4. **Migration applies cleanly** against a database that already has 001
   and 002: `python -m pipeline migrate` reports `003_conversation_turns.sql`
   applied, and a second run reports nothing pending.
5. **Manual multi-turn check** (needs a real `ANTHROPIC_API_KEY`, the full
   corpus DB, `docker compose up -d`, backend `uvicorn`, and
   `npm run dev`). Run the spec §4 script by hand:
   - Ask *"What was Apple's total net sales in fiscal 2024?"* — expect a
     verified answer, no "Searched for:" caption (first turn).
   - Ask *"and in fiscal 2023?"* — expect the caption to read a
     self-contained fiscal-2023 question, and the answer to cite Apple's
     fiscal-2023 10-K, not the 2024 one.
   - Ask *"how does that compare to Microsoft?"* — expect the caption to
     name both companies and the answer to rest on filings from each.
   - Click "New conversation" — the thread clears; the next question gets
     no caption.
   Record what the captions actually said in the branch's PR description —
   this is the evidence that stands in for an eval number.
6. **Known non-goals to *not* fix now** (spec §7): no multi-turn
   golden-set format, no conversation rehydration after a reload (the
   thread is empty in the UI though the backend still continues it), no
   retention/cleanup policy, no editing past turns.
7. Land via PR (no AI attribution in the body either). Post-merge:
   fast-forward local `main`, delete the `conversation-memory` branch.
