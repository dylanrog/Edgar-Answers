import os
from datetime import date

import psycopg
import pytest
from tests.fakes import FakeEmbedder

from api.queries import load_filings_for_ticker
from pipeline import db, store
from pipeline.canonicalize import CanonicalFiling, Sentence
from pipeline.chunk import Chunk
from pipeline.companies import Company
from pipeline.edgar import FilingRef

COMPANY = Company(999999006, "TSTF", "Test Co F")


def seed_filing(conn, accession, filing_date, period_end, form_type="10-Q"):
    text = f"Filing {accession} reports quarterly results."
    sentence = Sentence(0, "item1", text, 0, len(text))
    canonical = CanonicalFiling(
        text, [sentence], f'<p><span data-sid="0">{text}</span></p>'
    )
    ref = FilingRef(
        cik=COMPANY.cik,
        accession=accession,
        form_type=form_type,
        filing_date=filing_date,
        period_end=period_end,
        primary_document="t.htm",
    )
    filing_id = store.store_filing(conn, COMPANY, ref, canonical)
    store.store_chunks(
        conn, filing_id, [Chunk("item1", 0, 0, text, 10)],
        FakeEmbedder().embed_texts([text]),
    )


@pytest.fixture()
def seeded_conn():
    conn = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    db.migrate(conn)
    with conn.cursor() as cur:
        for accession in ("TESTF-24-000001", "TESTF-24-000002"):
            cur.execute(
                "DELETE FROM chunks WHERE filing_id IN"
                " (SELECT id FROM filings WHERE accession = %s)",
                (accession,),
            )
            cur.execute(
                "DELETE FROM sentences WHERE filing_id IN"
                " (SELECT id FROM filings WHERE accession = %s)",
                (accession,),
            )
            cur.execute("DELETE FROM filings WHERE accession = %s", (accession,))
    conn.commit()
    seed_filing(conn, "TESTF-24-000001", date(2024, 2, 1), date(2023, 12, 30))
    seed_filing(conn, "TESTF-24-000002", date(2024, 5, 1), date(2024, 3, 30))
    yield conn
    conn.close()


@pytest.mark.db
def test_load_filings_for_ticker_returns_oldest_first_with_all_fields(seeded_conn):
    filings = load_filings_for_ticker(seeded_conn, "TSTF")
    assert [f["accession"] for f in filings] == ["TESTF-24-000001", "TESTF-24-000002"]
    assert filings[0]["form_type"] == "10-Q"
    assert filings[0]["filing_date"] == "2024-02-01"
    assert filings[0]["period_end"] == "2023-12-30"


@pytest.mark.db
def test_load_filings_for_ticker_returns_empty_for_unknown_ticker(seeded_conn):
    assert load_filings_for_ticker(seeded_conn, "ZZZZ") == []
