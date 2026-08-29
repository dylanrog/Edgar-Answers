import os
from datetime import date
from pathlib import Path

import psycopg
import pytest

from pipeline import db, ingest, store
from pipeline.canonicalize import canonicalize
from pipeline.chunk import chunk_sentences
from pipeline.companies import Company
from pipeline.edgar import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY = Company(999999303, "TSTP", "Reprocess Test Co")
REF = FilingRef(
    cik=999999303,
    accession="REPROCESS-TEST-0001",
    form_type="10-K",
    filing_date=date(2024, 11, 1),
    period_end=date(2024, 9, 28),
    primary_document="table_shapes.html",
)


class FakeEmbedder:
    def embed_texts(self, texts):
        return [[0.0] * 384 for _ in texts]


def _seed(conn, cache_dir: Path) -> int:
    raw = (FIXTURES / "table_shapes.html").read_text(encoding="utf-8")
    cached = cache_dir / str(REF.cik) / f"{REF.accession}.html"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(raw, encoding="utf-8")
    canonical = canonicalize(raw, REF.form_type)
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    chunks = chunk_sentences(canonical.sentences)
    store.store_chunks(conn, filing_id, chunks, [[0.0] * 384 for _ in chunks])
    return filing_id


def _cleanup(conn):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM chunks WHERE filing_id IN"
            " (SELECT id FROM filings WHERE accession = %s)", (REF.accession,)
        )
        cur.execute(
            "DELETE FROM sentences WHERE filing_id IN"
            " (SELECT id FROM filings WHERE accession = %s)", (REF.accession,)
        )
        cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
        cur.execute("DELETE FROM companies WHERE cik = %s", (COMPANY.cik,))
    conn.commit()


@pytest.mark.db
def test_reprocess_rebuilds_sentences_chunks_and_embeddings(tmp_path):
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        filing_id = _seed(conn, tmp_path)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sentences WHERE filing_id = %s AND sid > 1", (filing_id,))
        conn.commit()

        stats = ingest.reprocess_filings(
            conn, FakeEmbedder(), cache_dir=tmp_path, ticker="TSTP"
        )
        conn.commit()

        assert stats.reprocessed == 1
        assert stats.missing == 0
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM sentences WHERE filing_id = %s", (filing_id,))
            assert cur.fetchone()[0] > 2, "sentences were not rebuilt"
            cur.execute("SELECT count(*) FROM chunks WHERE filing_id = %s", (filing_id,))
            assert cur.fetchone()[0] >= 1
        _cleanup(conn)


@pytest.mark.db
def test_reprocess_dry_run_writes_nothing(tmp_path):
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        filing_id = _seed(conn, tmp_path)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sentences WHERE filing_id = %s AND sid > 1", (filing_id,))
            cur.execute("SELECT count(*) FROM sentences WHERE filing_id = %s", (filing_id,))
            before = cur.fetchone()[0]
        conn.commit()

        stats = ingest.reprocess_filings(
            conn, FakeEmbedder(), cache_dir=tmp_path, ticker="TSTP", dry_run=True
        )
        conn.commit()

        assert stats.reprocessed == 0
        assert stats.moved and stats.moved[0][0] == REF.accession
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM sentences WHERE filing_id = %s", (filing_id,))
            assert cur.fetchone()[0] == before
        _cleanup(conn)


@pytest.mark.db
def test_reprocess_skips_a_filing_missing_from_the_cache(tmp_path):
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        _seed(conn, tmp_path)
        conn.commit()
        (tmp_path / str(REF.cik) / f"{REF.accession}.html").unlink()

        stats = ingest.reprocess_filings(
            conn, FakeEmbedder(), cache_dir=tmp_path, ticker="TSTP"
        )
        conn.commit()
        assert stats.reprocessed == 0
        assert stats.missing == 1
        _cleanup(conn)
