"""Column-binding coverage over stored tables (spec 2026-09-29 §6).

No pass/fail threshold: it measures binding beyond the fixtures and tells the
arithmetic spec how much of the corpus it can verify.
"""

from __future__ import annotations

_JOIN = " JOIN filings f ON f.id = {alias}.filing_id JOIN companies c ON c.cik = f.cik"


def _share(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def table_report(conn, *, ticker: str | None = None) -> dict:
    where = " WHERE c.ticker = %s" if ticker else ""
    params = [ticker.upper()] if ticker else []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(tc.column_label) FROM table_cells tc"
            + _JOIN.format(alias="tc") + where,
            params,
        )
        cells, labelled = cur.fetchone()
        cur.execute(
            "SELECT count(*), count(ft.scale), count(*) FILTER (WHERE ft.splittable)"
            " FROM filing_tables ft" + _JOIN.format(alias="ft") + where,
            params,
        )
        tables, scaled, splittable = cur.fetchone()
        cur.execute(
            "SELECT c.ticker, f.accession, count(*), count(tc.column_label)"
            " FROM table_cells tc" + _JOIN.format(alias="tc") + where
            + " GROUP BY c.ticker, f.accession"
            " ORDER BY count(tc.column_label)::float / count(*), f.accession LIMIT 10",
            params,
        )
        worst = [
            (row_ticker, accession, n, _share(n_labelled, n))
            for row_ticker, accession, n, n_labelled in cur.fetchall()
        ]
    return {
        "cells": cells,
        "labelled_share": _share(labelled, cells),
        "tables": tables,
        "scaled_share": _share(scaled, tables),
        "splittable_share": _share(splittable, tables),
        "worst": worst,
    }
