-- Which table a sentence came from, or NULL for prose. The chunker uses it to
-- keep a table's rows in one chunk so a header row always travels with its
-- data (spec 2026-08-12 §4.3).
ALTER TABLE sentences ADD COLUMN table_id integer;
