import os
from datetime import date

import psycopg
import pytest
from tests.fakes import (
    FakeEmbedder,
    StubCompanyDetector,
    StubConversationRewriter,
    StubGenerator,
)

from api.answer import answer_stream
from api.conversation import load_recent_turns, save_turn
from pipeline import db, store
from pipeline.canonicalize import CanonicalFiling, Sentence
from pipeline.chunk import Chunk
from pipeline.companies import Company
from pipeline.edgar import FilingRef

COMPANY = Company(999999005, "TSTE", "Test Co E")
ACCESSION = "9999999999-24-000005"
SENTENCES = [
    "Total net sales were 391.0 billion dollars in fiscal 2024.",
    "Services revenue reached an all-time record.",
]
CHUNK_TEXT = " ".join(SENTENCES)


@pytest.fixture()
def seeded_conn():
    conn = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    db.migrate(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM conversation_turns")
        cur.execute("DELETE FROM chunks")
        cur.execute("DELETE FROM sentences")
        cur.execute("DELETE FROM filings")
        cur.execute("DELETE FROM companies")
    conn.commit()
    sentences = []
    cursor = 0
    for sid, text in enumerate(SENTENCES):
        sentences.append(Sentence(sid, "item7", text, cursor, cursor + len(text)))
        cursor += len(text) + 1
    canonical = CanonicalFiling(
        "\n".join(SENTENCES),
        sentences,
        "".join(f'<p><span data-sid="{s.sid}">{s.text}</span></p>' for s in sentences),
    )
    ref = FilingRef(
        cik=COMPANY.cik,
        accession=ACCESSION,
        form_type="10-K",
        filing_date=date(2024, 11, 1),
        period_end=date(2024, 9, 28),
        primary_document="t.htm",
    )
    filing_id = store.store_filing(conn, COMPANY, ref, canonical)
    store.store_chunks(
        conn,
        filing_id,
        [Chunk("item7", 0, 1, CHUNK_TEXT, 20)],
        FakeEmbedder().embed_texts([CHUNK_TEXT]),
    )
    conn.commit()
    yield conn
    conn.close()


def response_with(quote: str, chunk_id: int) -> str:
    return (
        "Total net sales were $391.0 billion [1].\n\n"
        "```json\n"
        '{"citations": [{"marker": 1, "chunk_id": CHUNK_ID, "quote": "QUOTE"}]}\n'
        "```"
    ).replace("CHUNK_ID", str(chunk_id)).replace("QUOTE", quote)


def chunk_id_of(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM chunks LIMIT 1")
        return cur.fetchone()[0]


def collect(conn, *responses):
    generator = StubGenerator(*responses)
    events = list(
        answer_stream(
            conn,
            FakeEmbedder(),
            generator,
            StubCompanyDetector(),
            "What were total net sales?",
        )
    )
    return events, generator


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


@pytest.mark.db
def test_streams_tokens_then_a_verified_citation_then_done(seeded_conn):
    quote = "Total net sales were 391.0 billion dollars"
    events, _ = collect(seeded_conn, response_with(quote, chunk_id_of(seeded_conn)))
    names = [e.name for e in events]
    assert names[0] == "token"
    assert names[-1] == "done"
    answer = "".join(e.data["text"] for e in events if e.name == "token")
    assert "391.0 billion" in answer
    assert "```" not in answer
    citations = [e for e in events if e.name == "citation"]
    assert len(citations) == 1
    assert citations[0].data["verified"] is True
    assert citations[0].data["sids"] == [0]
    assert citations[0].data["accession"] == ACCESSION


@pytest.mark.db
def test_fabricated_quote_is_reported_unverified_not_dropped(seeded_conn):
    events, _ = collect(
        seeded_conn,
        response_with("Net sales collapsed by half", chunk_id_of(seeded_conn)),
    )
    citations = [e for e in events if e.name == "citation"]
    assert len(citations) == 1
    assert citations[0].data["verified"] is False
    assert citations[0].data["sids"] == []


@pytest.mark.db
def test_citation_naming_an_unretrieved_chunk_is_unverified(seeded_conn):
    events, _ = collect(seeded_conn, response_with("Total net sales", 987654))
    citations = [e for e in events if e.name == "citation"]
    assert citations[0].data["verified"] is False


@pytest.mark.db
def test_unparseable_block_retries_once_then_succeeds(seeded_conn):
    good = response_with("Services revenue reached", chunk_id_of(seeded_conn))
    events, generator = collect(seeded_conn, "No block here at all.", good)
    assert generator.calls == 2
    assert [e for e in events if e.name == "citation"]
    assert next(e for e in events if e.name == "done").data["citations_total"] == 1


@pytest.mark.db
def test_unparseable_block_twice_degrades_without_erroring(seeded_conn):
    events, generator = collect(seeded_conn, "Nope.", "Still nope.")
    assert generator.calls == 2
    done = next(e for e in events if e.name == "done")
    assert done.data["citations_total"] == 0
    assert done.data["unverified_answer"] is True
    assert not [e for e in events if e.name == "error"]


@pytest.mark.db
def test_done_event_carries_the_counts(seeded_conn):
    quote = "Services revenue reached an all-time record."
    events, _ = collect(seeded_conn, response_with(quote, chunk_id_of(seeded_conn)))
    done = next(e for e in events if e.name == "done").data
    assert done["chunks_retrieved"] == 1
    assert done["citations_total"] == 1
    assert done["citations_verified"] == 1


@pytest.mark.db
def test_generator_failure_becomes_an_error_event(seeded_conn):
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
        )
    )
    assert events[-1].name == "error"
    assert "message" in events[-1].data


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
