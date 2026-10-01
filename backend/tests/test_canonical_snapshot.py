"""Spec 2026-09-29 §4.1: column binding must not move a single byte of what
the canonicalizer already produces. The snapshot is recorded from the code
*before* the change; this test holds the change to it."""

import hashlib
import json
from pathlib import Path

from pipeline.canonicalize import canonicalize

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = FIXTURES / "canonical_snapshot.json"


def _digest(raw: str) -> dict:
    canonical = canonicalize(raw, "10-K")
    sentences = [
        [s.sid, s.section, s.text, s.char_start, s.char_end, s.table_id]
        for s in canonical.sentences
    ]
    return {
        "sentences": hashlib.sha256(json.dumps(sentences).encode()).hexdigest(),
        "canonical_text": hashlib.sha256(canonical.canonical_text.encode()).hexdigest(),
        "viewer_html": hashlib.sha256(canonical.viewer_html.encode()).hexdigest(),
    }


def current() -> dict:
    return {
        path.name: _digest(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURES.glob("*.html"))
    }


def write_snapshot() -> None:
    SNAPSHOT.write_text(json.dumps(current(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_column_binding_leaves_sentences_and_viewer_html_byte_identical():
    assert current() == json.loads(SNAPSHOT.read_text(encoding="utf-8"))
