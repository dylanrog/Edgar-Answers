import logging
import os
from datetime import date

import psycopg
import pytest
from tests.fakes import (
    FakeEmbedder,
    StubCompanyDetector,
    StubConversationRewriter,
    StubGenerator,
    StubPeriodDetector,
    StubQuoteRepairer,
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
    assert next(n for n in names if n != "progress") == "token"
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
def test_generator_failure_is_logged_with_its_traceback(seeded_conn, caplog):
    # The stream has already returned 200, so without this log line a failed
    # answer is invisible in the server logs (`fly logs`).
    class Boom:
        def stream(self, system, user):
            raise RuntimeError("upstream is down")
            yield  # pragma: no cover

    with caplog.at_level(logging.ERROR, logger="api.answer"):
        list(
            answer_stream(
                seeded_conn, FakeEmbedder(), Boom(), StubCompanyDetector(), "What were net sales?"
            )
        )
    [record] = [r for r in caplog.records if r.name == "api.answer"]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None and "upstream is down" in str(record.exc_info[1])


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


def progress(events, stage):
    return [e.data for e in events if e.name == "progress" and e.data["stage"] == stage]


@pytest.mark.db
def test_progress_events_arrive_in_stage_order(seeded_conn):
    quote = "Total net sales were 391.0 billion dollars"
    events, _ = collect(seeded_conn, response_with(quote, chunk_id_of(seeded_conn)))
    names = [e.name for e in events]
    stages = [e.data["stage"] for e in events if e.name == "progress"]
    assert stages == ["targets", "retrieved", "verifying"]
    progress_at = {e.data["stage"]: i for i, e in enumerate(events) if e.name == "progress"}
    token_at = [i for i, n in enumerate(names) if n == "token"]
    assert progress_at["retrieved"] < token_at[0]
    assert token_at[-1] < progress_at["verifying"] < names.index("citation")


@pytest.mark.db
def test_retrieved_progress_counts_passages_by_filing(seeded_conn):
    quote = "Total net sales were 391.0 billion dollars"
    events, _ = collect(seeded_conn, response_with(quote, chunk_id_of(seeded_conn)))
    (retrieved,) = progress(events, "retrieved")
    assert retrieved["passages"] == 1
    assert retrieved["filings"] == [
        {
            "accession": ACCESSION,
            "ticker": "TSTE",
            "form_type": "10-K",
            "filing_date": "2024-11-01",
            "passages": 1,
        }
    ]


@pytest.mark.db
def test_targets_progress_lists_tickers_and_no_narrowing_without_a_period_detector(
    seeded_conn,
):
    events = list(
        answer_stream(
            seeded_conn,
            FakeEmbedder(),
            StubGenerator("Nope."),
            StubCompanyDetector(),
            "What were total net sales?",
            tickers=["TSTE"],
        )
    )
    (targets,) = progress(events, "targets")
    assert targets["tickers"] == ["TSTE"]
    assert targets["filings"] == []


@pytest.mark.db
def test_targets_progress_lists_narrowed_filings(seeded_conn):
    question = "What were total net sales?"
    events = list(
        answer_stream(
            seeded_conn,
            FakeEmbedder(),
            StubGenerator("Nope."),
            StubCompanyDetector(),
            question,
            tickers=["TSTE"],
            period_detector=StubPeriodDetector({question: [ACCESSION]}),
        )
    )
    (targets,) = progress(events, "targets")
    assert targets["filings"] == [
        {
            "accession": ACCESSION,
            "ticker": "TSTE",
            "form_type": "10-K",
            "filing_date": "2024-11-01",
        }
    ]


@pytest.mark.db
def test_verifying_progress_counts_zero_when_the_block_never_parses(seeded_conn):
    events, _ = collect(seeded_conn, "Nope.", "Still nope.")
    (verifying,) = progress(events, "verifying")
    assert verifying["quotes"] == 0


# --- Citation repair (design §6.3 step 5) -----------------------------------

GOOD_QUOTE = "Total net sales were 391.0 billion dollars"
# True claim, wrong words: what the repair step exists for.
MISQUOTE = "Total net sales were $391 billion"


def ask_with_repairer(conn, response, repairer):
    return list(
        answer_stream(
            conn,
            FakeEmbedder(),
            StubGenerator(response),
            StubCompanyDetector(),
            "What were total net sales?",
            quote_repairer=repairer,
        )
    )


def citation_events(events):
    return [e.data for e in events if e.name == "citation"]


@pytest.mark.db
def test_a_misquote_is_repaired_against_the_same_excerpt_and_verified(seeded_conn):
    repairer = StubQuoteRepairer(GOOD_QUOTE)
    events = ask_with_repairer(
        seeded_conn, response_with(MISQUOTE, chunk_id_of(seeded_conn)), repairer
    )
    [citation] = citation_events(events)
    assert citation["verified"] is True
    assert citation["repaired"] is True
    assert citation["quote"] == GOOD_QUOTE
    assert citation["sids"] == [0]
    assert citation["accession"] == ACCESSION
    done = next(e for e in events if e.name == "done").data
    assert done["citations_verified"] == 1


@pytest.mark.db
def test_the_repair_sees_the_claim_the_failed_quote_and_only_the_cited_chunk(seeded_conn):
    repairer = StubQuoteRepairer(GOOD_QUOTE)
    ask_with_repairer(seeded_conn, response_with(MISQUOTE, chunk_id_of(seeded_conn)), repairer)
    [(claim, quote, excerpt)] = repairer.calls
    assert "Total net sales were $391.0 billion" in claim
    assert "[1]" not in claim
    assert quote == MISQUOTE
    assert excerpt == CHUNK_TEXT


@pytest.mark.db
def test_a_repair_that_still_does_not_verify_keeps_the_original_quote(seeded_conn):
    repairer = StubQuoteRepairer("Total net sales were 400 billion dollars")
    events = ask_with_repairer(
        seeded_conn, response_with(MISQUOTE, chunk_id_of(seeded_conn)), repairer
    )
    [citation] = citation_events(events)
    assert len(repairer.calls) == 1
    assert citation["verified"] is False
    assert citation["repaired"] is False
    assert citation["quote"] == MISQUOTE
    assert citation["sids"] == []
    assert citation["cells"] == []


@pytest.mark.db
def test_a_repairer_answering_none_keeps_the_original_unverified(seeded_conn):
    repairer = StubQuoteRepairer(None)
    events = ask_with_repairer(
        seeded_conn, response_with(MISQUOTE, chunk_id_of(seeded_conn)), repairer
    )
    [citation] = citation_events(events)
    assert citation["verified"] is False
    assert citation["quote"] == MISQUOTE


@pytest.mark.db
def test_a_repair_over_the_quote_cap_is_not_used(seeded_conn):
    # Even a stub that skips parse_repaired_quote can't slip a long quote by.
    repairer = StubQuoteRepairer(CHUNK_TEXT + " " + "x" * 300)
    events = ask_with_repairer(
        seeded_conn, response_with(MISQUOTE, chunk_id_of(seeded_conn)), repairer
    )
    [citation] = citation_events(events)
    assert citation["verified"] is False
    assert citation["quote"] == MISQUOTE


@pytest.mark.db
def test_a_repairer_failure_degrades_to_the_original_unverified(seeded_conn, caplog):
    repairer = StubQuoteRepairer(error=TimeoutError("repair timed out"))
    with caplog.at_level(logging.WARNING, logger="api.answer"):
        events = ask_with_repairer(
            seeded_conn, response_with(MISQUOTE, chunk_id_of(seeded_conn)), repairer
        )
    [citation] = citation_events(events)
    assert citation["verified"] is False
    assert citation["quote"] == MISQUOTE
    assert not [e for e in events if e.name == "error"]
    assert events[-1].name == "done"
    assert "repair timed out" in caplog.text


@pytest.mark.db
def test_a_verified_citation_is_never_sent_to_repair(seeded_conn):
    repairer = StubQuoteRepairer("Services revenue reached an all-time record.")
    events = ask_with_repairer(
        seeded_conn, response_with(GOOD_QUOTE, chunk_id_of(seeded_conn)), repairer
    )
    [citation] = citation_events(events)
    assert repairer.calls == []
    assert citation["quote"] == GOOD_QUOTE
    assert citation["repaired"] is False


@pytest.mark.db
def test_a_citation_naming_an_unretrieved_chunk_is_not_repaired(seeded_conn):
    repairer = StubQuoteRepairer(GOOD_QUOTE)
    events = ask_with_repairer(seeded_conn, response_with(MISQUOTE, 987654), repairer)
    [citation] = citation_events(events)
    assert repairer.calls == []
    assert citation["verified"] is False


@pytest.mark.db
def test_repair_calls_per_answer_are_capped(seeded_conn):
    chunk_id = chunk_id_of(seeded_conn)
    prose = " ".join(f"Segment {n} sales were $391.0 billion [{n}]." for n in range(1, 6))
    block = ", ".join(
        f'{{"marker": {n}, "chunk_id": {chunk_id}, "quote": "misquote {n}"}}'
        for n in range(1, 6)
    )
    response = f'{prose}\n\n```json\n{{"citations": [{block}]}}\n```'
    repairer = StubQuoteRepairer(GOOD_QUOTE)
    events = ask_with_repairer(seeded_conn, response, repairer)
    citations = citation_events(events)
    assert len(repairer.calls) == 3
    assert [c["verified"] for c in citations] == [True, True, True, False, False]
    assert [c["quote"] for c in citations[3:]] == ["misquote 4", "misquote 5"]


@pytest.mark.db
def test_without_a_repairer_a_misquote_stays_unverified(seeded_conn):
    events, _ = collect(seeded_conn, response_with(MISQUOTE, chunk_id_of(seeded_conn)))
    [citation] = citation_events(events)
    assert citation["verified"] is False
    assert citation["repaired"] is False


def ask_prose_with_repairer(conn, prose, quote, repairer, *, responses=()):
    response = (
        f"{prose}\n\n```json\n"
        f'{{"citations": [{{"marker": 1, "chunk_id": {chunk_id_of(conn)}, "quote": "{quote}"}}]}}'
        "\n```"
    )
    return list(
        answer_stream(
            conn,
            FakeEmbedder(),
            StubGenerator(*responses, response) if responses else StubGenerator(response),
            StubCompanyDetector(),
            "What were total net sales?",
            quote_repairer=repairer,
        )
    )


@pytest.mark.db
def test_a_repair_cannot_launder_a_figure_the_source_does_not_give(seeded_conn):
    # The claim invents $412.7 billion; the repairer finds the real sentence,
    # which verifies -- but it states 391.0, not the claim's figure, so the
    # badge that caught the invented figure must stay.
    repairer = StubQuoteRepairer(GOOD_QUOTE)
    events = ask_prose_with_repairer(
        seeded_conn,
        "Total net sales were $412.7 billion [1].",
        "Total net sales were $412.7 billion",
        repairer,
    )
    [citation] = citation_events(events)
    assert len(repairer.calls) == 1
    assert citation["verified"] is False
    assert citation["repaired"] is False
    assert citation["quote"] == "Total net sales were $412.7 billion"
    assert citation["sids"] == []


@pytest.mark.db
def test_a_rounded_claim_figure_accepts_the_precise_quote(seeded_conn):
    repairer = StubQuoteRepairer(GOOD_QUOTE)
    events = ask_prose_with_repairer(
        seeded_conn, "Total net sales were $391 billion [1].", MISQUOTE, repairer
    )
    [citation] = citation_events(events)
    assert citation["verified"] is True
    assert citation["repaired"] is True


@pytest.mark.db
def test_a_claim_with_no_figure_is_never_repaired(seeded_conn):
    repairer = StubQuoteRepairer("Services revenue reached an all-time record.")
    events = ask_prose_with_repairer(
        seeded_conn,
        "In Q2 services revenue reached a record [1].",
        "Services revenue hit a record",
        repairer,
    )
    [citation] = citation_events(events)
    assert repairer.calls == []
    assert citation["verified"] is False
    assert citation["quote"] == "Services revenue hit a record"


@pytest.mark.db
def test_a_marker_missing_from_the_prose_is_not_repaired(seeded_conn):
    repairer = StubQuoteRepairer(GOOD_QUOTE)
    events = ask_prose_with_repairer(
        seeded_conn, "Total net sales were $391.0 billion.", MISQUOTE, repairer
    )
    [citation] = citation_events(events)
    assert repairer.calls == []
    assert citation["verified"] is False


@pytest.mark.db
def test_citations_from_the_retry_are_not_repaired(seeded_conn):
    # Attempt 1 streams prose but no parseable block; the retry's citations
    # belong to prose the visitor never saw, so nothing there is repaired.
    repairer = StubQuoteRepairer(GOOD_QUOTE)
    events = ask_prose_with_repairer(
        seeded_conn,
        "Total net sales were $391.0 billion [1].",
        MISQUOTE,
        repairer,
        responses=("Total net sales were $391.0 billion [1].\n\n```json\nnot json\n```",),
    )
    [citation] = citation_events(events)
    assert repairer.calls == []
    assert citation["verified"] is False
    assert citation["quote"] == MISQUOTE


@pytest.mark.db
def test_no_new_repair_starts_after_the_per_answer_budget(seeded_conn, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("api.answer._clock", lambda: clock[0])

    def slow(quote):
        clock[0] += 11.0  # each repair takes longer than the whole budget
        return GOOD_QUOTE

    chunk_id = chunk_id_of(seeded_conn)
    prose = " ".join(f"Segment {n} sales were $391.0 billion [{n}]." for n in range(1, 4))
    block = ", ".join(
        f'{{"marker": {n}, "chunk_id": {chunk_id}, "quote": "misquote {n}"}}'
        for n in range(1, 4)
    )
    response = f'{prose}\n\n```json\n{{"citations": [{block}]}}\n```'
    repairer = StubQuoteRepairer(slow)
    citations = citation_events(ask_with_repairer(seeded_conn, response, repairer))
    assert len(repairer.calls) == 1
    assert [c["verified"] for c in citations] == [True, False, False]
