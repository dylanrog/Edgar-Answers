import os
from datetime import date
from pathlib import Path

import psycopg
import pytest

from pipeline import db, report, store
from pipeline.canonicalize import canonicalize
from pipeline.companies import Company
from pipeline.edgar import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY = Company(999999403, "TSTZ", "Report Test Co")
REF = FilingRef(
    cik=999999403,
    accession="REPORT-TEST-0001",
    form_type="10-K",
    filing_date=date(2025, 2, 26),
    period_end=None,
    primary_document="x.html",
)


@pytest.mark.db
def test_table_report_summarises_one_tickers_cells_and_tables():
    canonical = canonicalize(
        (FIXTURES / "edgar_msft_segments.html").read_text(encoding="utf-8"), "10-K"
    )
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        store.store_filing(conn, COMPANY, REF, canonical, replace=True)
        conn.commit()
        try:
            result = report.table_report(conn, ticker="TSTZ")
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM sentences WHERE filing_id IN"
                    " (SELECT id FROM filings WHERE accession = %s)",
                    (REF.accession,),
                )
                cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
            conn.commit()

    assert result["cells"] == len(canonical.cells) == 12
    assert result["labelled_share"] == 1.0
    assert (result["tables"], result["scaled_share"], result["splittable_share"]) == (1, 1.0, 1.0)
    assert result["worst"] == [("TSTZ", "REPORT-TEST-0001", 12, 1.0)]
