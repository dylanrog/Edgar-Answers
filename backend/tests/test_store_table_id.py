import os
from datetime import date

import psycopg
import pytest

from pipeline import db, store
from pipeline.canonicalize import CanonicalFiling, Sentence
from pipeline.companies import Company
from pipeline.edgar import FilingRef

COMPANY = Company(999999302, "TSTT", "Table Id Test Co")
REF = FilingRef(
    cik=999999302,
    accession="TABLEID-TEST-0001",
    form_type="10-K",
    filing_date=date(2024, 11, 1),
    period_end=date(2024, 9, 28),
    primary_document="x.html",
)


@pytest.mark.db
def test_table_id_survives_the_database_round_trip():
    """recanonicalize compares freshly computed sentences against stored rows
    for equality, so a field that does not persist would make every filing
    look mismatched."""
    sentences = [
        Sentence(0, "item7", "Net sales grew.", 0, 15, None),
        Sentence(1, "item7", "Americas $ 162,560", 16, 34, 1),
        Sentence(2, "item7", "Europe $ 94,294", 35, 50, 1),
    ]
    canonical = CanonicalFiling("irrelevant", sentences, "<p>x</p>")
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
        conn.commit()

        assert store.load_sentences(conn, filing_id) == sentences

        with conn.cursor() as cur:
            cur.execute("DELETE FROM sentences WHERE filing_id = %s", (filing_id,))
            cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
            cur.execute("DELETE FROM companies WHERE cik = %s", (COMPANY.cik,))
        conn.commit()
