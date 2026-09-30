import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from pipeline import db, store
from pipeline.canonicalize import canonicalize
from pipeline.companies import Company
from pipeline.edgar import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY = Company(999999401, "TSTX", "Cells Test Co")
REF = FilingRef(
    cik=999999401,
    accession="CELLS-TEST-0001",
    form_type="10-K",
    filing_date=date(2025, 2, 26),
    period_end=date(2025, 1, 26),
    primary_document="x.html",
)


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


def nvda():
    return canonicalize((FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8"), "10-K")


@pytest.mark.db
def test_tables_and_cells_survive_the_round_trip(conn):
    canonical = nvda()
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    conn.commit()
    assert store.load_tables(conn, filing_id) == {t.table_id: t for t in canonical.tables}
    assert store.load_cells(conn, filing_id) == sorted(
        canonical.cells, key=lambda c: (c.sid, c.col)
    )


@pytest.mark.db
def test_load_cells_can_be_limited_to_a_sid_range(conn):
    canonical = nvda()
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    conn.commit()
    target = next(c for c in canonical.cells if c.raw == "115,186")
    cells = store.load_cells(conn, filing_id, target.sid, target.sid)
    assert {c.sid for c in cells} == {target.sid}
    assert next(c for c in cells if c.col == 4).value == Decimal("115186")


@pytest.mark.db
def test_replacing_a_filing_cascades_its_tables_away(conn):
    canonical = nvda()
    store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    conn.commit()
    assert len(store.load_cells(conn, filing_id)) == len(canonical.cells)


@pytest.mark.db
def test_delete_derived_clears_tables_and_cells(conn):
    filing_id = store.store_filing(conn, COMPANY, REF, nvda(), replace=True)
    store.delete_derived(conn, filing_id)
    conn.commit()
    assert store.load_tables(conn, filing_id) == {}
    assert store.load_cells(conn, filing_id) == []
