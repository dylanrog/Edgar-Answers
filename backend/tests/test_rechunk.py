import os
from datetime import date
from pathlib import Path

import psycopg
import pytest
from tests.fakes import FakeEmbedder

from pipeline import db, ingest, store
from pipeline.canonicalize import canonicalize
from pipeline.chunk import chunk_sentences
from pipeline.companies import Company
from pipeline.edgar import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY = Company(999999405, "TSTV", "Rechunk Test Co")
REF = FilingRef(
    cik=999999405,
    accession="RECHUNK-TEST-0001",
    form_type="10-K",
    filing_date=date(2025, 2, 26),
    period_end=date(2025, 1, 26),
    primary_document="x.html",
)


class RecordingEmbedder(FakeEmbedder):
    def __init__(self):
        self.inputs: list[str] = []

    def embed_texts(self, texts):
        self.inputs.extend(texts)
        return super().embed_texts(texts)


@pytest.fixture()
def conn():
    connection = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    db.migrate(connection)
    yield connection
    with connection.cursor() as cur:
        cur.execute(
            "DELETE FROM chunks WHERE filing_id IN (SELECT id FROM filings WHERE accession = %s)",
            (REF.accession,),
        )
        cur.execute(
            "DELETE FROM sentences WHERE filing_id IN"
            " (SELECT id FROM filings WHERE accession = %s)",
            (REF.accession,),
        )
        cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
    connection.commit()
    connection.close()


@pytest.mark.db
def test_rechunk_rebuilds_chunks_with_context_and_embeds_it(conn):
    canonical = canonicalize(
        (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8"), "10-K"
    )
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    stale = chunk_sentences(canonical.sentences)  # the pre-change, context-free chunks
    store.store_chunks(conn, filing_id, stale, FakeEmbedder().embed_texts([c.text for c in stale]))
    conn.commit()

    embedder = RecordingEmbedder()
    filings, chunks = ingest.rechunk_filings(conn, embedder, ticker="TSTV")
    conn.commit()

    assert (filings, chunks) == (1, 1)
    with conn.cursor() as cur:
        cur.execute("SELECT context, text, table_id FROM chunks WHERE filing_id = %s", (filing_id,))
        rows = cur.fetchall()
    assert len(rows) == 1
    context, text, table_id = rows[0]
    assert context.startswith("Table: The following table summarizes revenue")
    assert "Columns: Year Ended › [Jan 26, 2025" in context
    assert table_id is None  # the fixture table fits, so it is not split
    assert embedder.inputs == [f"{context}\n{text}"]


@pytest.mark.db
def test_embed_filings_uses_stored_cells_for_context(conn):
    canonical = canonicalize(
        (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8"), "10-K"
    )
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    conn.commit()
    embedder = RecordingEmbedder()
    ingest.embed_filings(conn, embedder, ticker="TSTV")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT context FROM chunks WHERE filing_id = %s", (filing_id,))
        rows = cur.fetchall()
    assert len(rows) == 1
    context = rows[0][0]
    assert "Scale: in millions" in context
    assert embedder.inputs[0].startswith(context)
