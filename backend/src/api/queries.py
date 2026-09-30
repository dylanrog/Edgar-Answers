from __future__ import annotations

import psycopg

from pipeline.canonicalize import Sentence
from pipeline.store import load_cells
from pipeline.tables import Cell


def load_chunk_cells(
    conn: psycopg.Connection, filing_id: int, sid_start: int, sid_end: int
) -> list[Cell]:
    """The table cells a chunk's rows hold, in (sid, col) order."""
    return load_cells(conn, filing_id, sid_start, sid_end)


def load_chunk_sentences(
    conn: psycopg.Connection, filing_id: int, sid_start: int, sid_end: int
) -> list[Sentence]:
    """The sentences a chunk covers, in document order."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sid, section, text, char_start, char_end FROM sentences"
            " WHERE filing_id = %s AND sid BETWEEN %s AND %s ORDER BY sid",
            (filing_id, sid_start, sid_end),
        )
        return [Sentence(*row) for row in cur.fetchall()]


def load_filing(conn: psycopg.Connection, accession: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT f.accession, f.viewer_html, c.ticker, f.form_type,"
            " f.filing_date, f.period_end"
            " FROM filings f JOIN companies c ON c.cik = f.cik"
            " WHERE f.accession = %s",
            (accession,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "accession": row[0],
        "viewer_html": row[1],
        "ticker": row[2],
        "form_type": row[3],
        "filing_date": row[4].isoformat(),
        "period_end": row[5].isoformat() if row[5] else None,
    }


def load_companies(conn: psycopg.Connection) -> list[dict]:
    """Only companies that actually have filings -- the filter UI should not
    offer a ticker that returns nothing."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.cik, c.ticker, c.name, count(f.id) FROM companies c"
            " JOIN filings f ON f.cik = c.cik"
            " GROUP BY c.cik, c.ticker, c.name ORDER BY c.ticker"
        )
        return [
            {"cik": cik, "ticker": ticker, "name": name, "filings": count}
            for cik, ticker, name, count in cur.fetchall()
        ]


def load_filings_for_ticker(conn: psycopg.Connection, ticker: str) -> list[dict]:
    """A company's filings, oldest first -- the candidate list a period
    detector picks specific accessions from."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT f.accession, f.form_type, f.filing_date, f.period_end"
            " FROM filings f JOIN companies c ON c.cik = f.cik"
            " WHERE c.ticker = %s ORDER BY f.filing_date",
            (ticker.upper(),),
        )
        return [
            {
                "accession": accession,
                "form_type": form_type,
                "filing_date": filing_date.isoformat(),
                "period_end": period_end.isoformat() if period_end else None,
            }
            for accession, form_type, filing_date, period_end in cur.fetchall()
        ]
