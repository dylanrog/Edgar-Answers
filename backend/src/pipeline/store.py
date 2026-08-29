from __future__ import annotations

import psycopg

from .canonicalize import CanonicalFiling, Sentence
from .chunk import Chunk
from .companies import Company
from .edgar import FilingRef


def filing_exists(conn: psycopg.Connection, accession: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM filings WHERE accession = %s", (accession,))
        return cur.fetchone() is not None


def store_filing(
    conn: psycopg.Connection,
    company: Company,
    ref: FilingRef,
    canonical: CanonicalFiling,
    *,
    replace: bool = False,
) -> int:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO companies (cik, ticker, name) VALUES (%s, %s, %s)"
            " ON CONFLICT (cik) DO NOTHING",
            (company.cik, company.ticker, company.name),
        )
        if replace:
            cur.execute(
                "DELETE FROM chunks WHERE filing_id IN"
                " (SELECT id FROM filings WHERE accession = %s)",
                (ref.accession,),
            )
            cur.execute(
                "DELETE FROM sentences WHERE filing_id IN"
                " (SELECT id FROM filings WHERE accession = %s)",
                (ref.accession,),
            )
            cur.execute("DELETE FROM filings WHERE accession = %s", (ref.accession,))
        cur.execute(
            "INSERT INTO filings (cik, accession, form_type, filing_date, period_end,"
            " viewer_html) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                company.cik,
                ref.accession,
                ref.form_type,
                ref.filing_date,
                ref.period_end,
                canonical.viewer_html,
            ),
        )
        filing_id = cur.fetchone()[0]
        with cur.copy(
            "COPY sentences (filing_id, sid, section, text, char_start, char_end,"
            " table_id) FROM STDIN"
        ) as copy:
            for s in canonical.sentences:
                copy.write_row(
                    (filing_id, s.sid, s.section, s.text, s.char_start, s.char_end, s.table_id)
                )
    return filing_id


def delete_derived(conn: psycopg.Connection, filing_id: int) -> None:
    """Drop a filing's chunks and sentences, keeping the filings row itself."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE filing_id = %s", (filing_id,))
        cur.execute("DELETE FROM sentences WHERE filing_id = %s", (filing_id,))


def replace_sentences(
    conn: psycopg.Connection, filing_id: int, sentences: list[Sentence]
) -> None:
    with conn.cursor() as cur, cur.copy(
        "COPY sentences (filing_id, sid, section, text, char_start, char_end,"
        " table_id) FROM STDIN"
    ) as copy:
        for s in sentences:
            copy.write_row(
                (filing_id, s.sid, s.section, s.text, s.char_start, s.char_end, s.table_id)
            )


def to_pgvector(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vector) + "]"


def load_sentences(conn: psycopg.Connection, filing_id: int) -> list[Sentence]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sid, section, text, char_start, char_end, table_id"
            " FROM sentences WHERE filing_id = %s ORDER BY sid",
            (filing_id,),
        )
        return [Sentence(*row) for row in cur.fetchall()]


def filing_ids_without_chunks(
    conn: psycopg.Connection, *, ticker: str | None = None
) -> list[int]:
    sql = (
        "SELECT f.id FROM filings f JOIN companies c ON c.cik = f.cik"
        " WHERE NOT EXISTS (SELECT 1 FROM chunks ch WHERE ch.filing_id = f.id)"
    )
    params: list[object] = []
    if ticker:
        sql += " AND c.ticker = %s"
        params.append(ticker.upper())
    sql += " ORDER BY f.id"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [row[0] for row in cur.fetchall()]


def filings_to_recanonicalize(
    conn: psycopg.Connection, *, ticker: str | None = None
) -> list[tuple[int, int, str, str]]:
    """(filing_id, cik, accession, form_type) for every stored filing."""
    sql = (
        "SELECT f.id, f.cik, f.accession, f.form_type FROM filings f"
        " JOIN companies c ON c.cik = f.cik"
    )
    params: list[object] = []
    if ticker:
        sql += " WHERE c.ticker = %s"
        params.append(ticker.upper())
    sql += " ORDER BY f.id"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def update_viewer_html(conn: psycopg.Connection, filing_id: int, viewer_html: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE filings SET viewer_html = %s WHERE id = %s", (viewer_html, filing_id)
        )


def store_chunks(
    conn: psycopg.Connection,
    filing_id: int,
    chunks: list[Chunk],
    vectors: list[list[float]],
) -> int:
    if len(chunks) != len(vectors):
        raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO chunks (filing_id, section, sid_start, sid_end, text,"
            " token_count, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s::vector)",
            [
                (
                    filing_id,
                    chunk.section,
                    chunk.sid_start,
                    chunk.sid_end,
                    chunk.text,
                    chunk.token_count,
                    to_pgvector(vector),
                )
                # Backstops the length check above: without strict, a future
                # refactor that drops that guard would silently truncate.
                for chunk, vector in zip(chunks, vectors, strict=True)
            ],
        )
    return len(chunks)
