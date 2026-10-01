-- Spec 2026-09-29 §5.4, amended on measurement: context stays embedded and shown to the
-- model, but the lexical arm searches chunk text only. Must stay spelled exactly like
-- api.retrieval._TSVECTOR (minus the ch. alias).
DROP INDEX chunks_text_fts;
CREATE INDEX chunks_text_fts ON chunks USING gin (to_tsvector('english', text));
