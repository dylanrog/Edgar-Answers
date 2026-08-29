import os
from datetime import date

import psycopg
import pytest
from tests.fakes import FakeEmbedder, StubCompanyDetector, StubGenerator

from api.answer import answer_stream
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
