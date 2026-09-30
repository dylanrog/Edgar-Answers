from pathlib import Path

from api.retrieval import _TSVECTOR

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_lexical_expression_matches_the_index_exactly():
    """A mismatch does not error -- the planner silently falls back to a
    sequential scan over every chunk. So the spelling is pinned."""
    sql = (MIGRATIONS / "004_table_cells.sql").read_text(encoding="utf-8")
    assert "to_tsvector('english', context || ' ' || text)" in sql
    assert _TSVECTOR.replace("ch.", "") == "to_tsvector('english', context || ' ' || text)"
