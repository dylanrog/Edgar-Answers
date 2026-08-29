import os
from datetime import date

import psycopg
import pytest

from pipeline import db, store
from pipeline.canonicalize import canonicalize
from pipeline.companies import Company
from pipeline.edgar import FilingRef

COMPANY = Company(999999301, "TSTR", "Replace Test Co")
REF = FilingRef(
    cik=999999301,
    accession="REPLACE-TEST-0001",
    form_type="10-K",
    filing_date=date(2024, 11, 1),
    period_end=date(2024, 9, 28),
    primary_document="x.html",
)
HTML = "<html><body><p>Net sales grew. Services grew too.</p></body></html>"


@pytest.mark.db
def test_replace_succeeds_when_the_filing_already_has_chunks():
    """Every filing in the real corpus is embedded, so this is the only path
    `ingest --force` and `reprocess` ever take. Without the chunks delete it
    raises ForeignKeyViolation on chunks_filing_id_fkey."""
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        canonical = canonicalize(HTML, "10-K")
        filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO chunks (filing_id, section, sid_start, sid_end, text,"
                " token_count, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s::vector)",
                (filing_id, "item7", 0, 1, "Net sales grew.", 4, "[" + ",".join(["0"] * 384) + "]"),
            )
        conn.commit()

        new_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chunks WHERE filing_id = %s", (filing_id,))
            assert cur.fetchone()[0] == 0, "stale chunks survived the replace"
            cur.execute("SELECT count(*) FROM sentences WHERE filing_id = %s", (new_id,))
            assert cur.fetchone()[0] == 2

        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE filing_id = %s", (new_id,))
            cur.execute("DELETE FROM sentences WHERE filing_id = %s", (new_id,))
            cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
            cur.execute("DELETE FROM companies WHERE cik = %s", (COMPANY.cik,))
        conn.commit()
