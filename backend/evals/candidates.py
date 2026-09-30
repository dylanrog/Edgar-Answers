"""Authoring aid for the table_tail golden questions (spec 2026-09-29 §7.1).

Lists table rows that start beyond MAX_TOKENS into their chunk -- rows the
vector arm cannot see in the current corpus, because bge-small truncates
there. The offset is a running sum of per-sentence token counts, which is
close enough to choose questions; nothing is scored with it.
"""

from __future__ import annotations

from pipeline.chunk import MAX_TOKENS, count_tokens


def tail_rows(
    sentences: list[tuple[int, str, int | None]], *, budget: int = MAX_TOKENS
) -> list[tuple[int, str, int]]:
    """(sid, text, token offset) for numeric table rows past `budget`.
    `sentences` is (sid, text, table_id) in chunk order."""
    out: list[tuple[int, str, int]] = []
    offset = 0
    for sid, text, table_id in sentences:
        if table_id is not None and offset >= budget and any(ch.isdigit() for ch in text):
            out.append((sid, text, offset))
        offset += count_tokens(text)
    return out


def tail_candidates(conn, *, ticker: str, limit: int = 40) -> list[tuple]:
    """(accession, form_type, filing_date, section, sid, offset, text) rows."""
    out: list[tuple] = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ch.filing_id, ch.sid_start, ch.sid_end, ch.section,"
            " f.accession, f.form_type, f.filing_date"
            " FROM chunks ch JOIN filings f ON f.id = ch.filing_id"
            " JOIN companies c ON c.cik = f.cik"
            " WHERE c.ticker = %s AND ch.token_count > %s"
            " ORDER BY f.filing_date DESC, ch.sid_start",
            (ticker.upper(), MAX_TOKENS),
        )
        chunks = cur.fetchall()
        for filing_id, sid_start, sid_end, section, accession, form_type, filed in chunks:
            cur.execute(
                "SELECT sid, text, table_id FROM sentences"
                " WHERE filing_id = %s AND sid BETWEEN %s AND %s ORDER BY sid",
                (filing_id, sid_start, sid_end),
            )
            for sid, text, offset in tail_rows(cur.fetchall()):
                out.append(
                    (accession, form_type, filed.isoformat(), section, sid, offset, text[:120])
                )
                if len(out) >= limit:
                    return out
    return out
