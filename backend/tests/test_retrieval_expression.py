from pathlib import Path

from api.retrieval import _TSVECTOR

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_lexical_expression_matches_the_index_exactly():
    """A mismatch does not error -- the planner silently falls back to a
    sequential scan over every chunk. So the spelling is pinned against the
    latest migration that defines the index."""
    defining = [
        p for p in sorted(MIGRATIONS.glob("*.sql"))
        if "CREATE INDEX chunks_text_fts" in p.read_text(encoding="utf-8")
    ]
    sql = defining[-1].read_text(encoding="utf-8")
    assert "USING gin (to_tsvector('english', text))" in sql
    assert _TSVECTOR.replace("ch.", "") == "to_tsvector('english', text)"
