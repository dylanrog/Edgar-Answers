import os
from datetime import date
from pathlib import Path

import psycopg
import pytest

from pipeline import db, ingest, store
from pipeline.canonicalize import CanonicalFiling, Sentence, canonicalize
from pipeline.companies import Company
from pipeline.edgar import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY = Company(999999402, "TSTY", "Retable Test Co")
REF = FilingRef(
    cik=999999402,
    accession="RETABLE-TEST-0001",
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
            "DELETE FROM sentences WHERE filing_id IN"
            " (SELECT id FROM filings WHERE accession = %s)",
            (REF.accession,),
        )
        cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
    connection.commit()
    connection.close()


def cache(tmp_path: Path) -> str:
    raw = (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8")
    path = tmp_path / str(REF.cik) / f"{REF.accession}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return raw


@pytest.mark.db
def test_retable_writes_cells_for_a_filing_stored_without_them(conn, tmp_path):
    raw = cache(tmp_path)
    canonical = canonicalize(raw, REF.form_type)
    bare = CanonicalFiling(canonical.canonical_text, canonical.sentences, canonical.viewer_html)
    filing_id = store.store_filing(conn, COMPANY, REF, bare, replace=True)
    conn.commit()
    assert store.load_cells(conn, filing_id) == []

    stats = ingest.retable_filings(conn, cache_dir=tmp_path, ticker="TSTY")
    conn.commit()

    assert (stats.updated, stats.missing, stats.mismatched) == (1, 0, [])
    assert len(store.load_cells(conn, filing_id)) == len(canonical.cells)


@pytest.mark.db
def test_retable_refuses_a_filing_whose_sentences_moved(conn, tmp_path):
    cache(tmp_path)
    stale = CanonicalFiling("x", [Sentence(0, "item7", "Different text.", 0, 15)], "<p>x</p>")
    filing_id = store.store_filing(conn, COMPANY, REF, stale, replace=True)
    conn.commit()

    stats = ingest.retable_filings(conn, cache_dir=tmp_path, ticker="TSTY")
    conn.commit()

    assert stats.mismatched == [REF.accession]
    assert store.load_tables(conn, filing_id) == {}


@pytest.mark.db
def test_retable_counts_a_filing_missing_from_the_cache(conn, tmp_path):
    canonical = canonicalize(
        (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8"), REF.form_type
    )
    store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    conn.commit()
    stats = ingest.retable_filings(conn, cache_dir=tmp_path, ticker="TSTY")
    assert (stats.updated, stats.missing) == (0, 1)
