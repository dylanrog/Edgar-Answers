-- Column binding (spec 2026-09-29 §4.2). ON DELETE CASCADE so every existing
-- "delete the filing" path -- store_filing(replace=True), test cleanups --
-- also clears its tables and cells without having to know they exist.
CREATE TABLE filing_tables (
  filing_id  bigint  NOT NULL REFERENCES filings (id) ON DELETE CASCADE,
  table_id   integer NOT NULL,
  caption    text,
  scale      numeric,
  splittable boolean NOT NULL,
  PRIMARY KEY (filing_id, table_id)
);

CREATE TABLE table_cells (
  filing_id     bigint  NOT NULL,
  sid           integer NOT NULL,
  col           integer NOT NULL,
  cell_index    integer NOT NULL,
  char_start    integer,
  char_end      integer,
  table_id      integer NOT NULL,
  raw           text    NOT NULL,
  value         numeric,
  kind          text    NOT NULL CHECK (kind IN ('number', 'percent', 'nil')),
  row_label     text,
  column_label  text,
  scale_applies boolean NOT NULL,
  PRIMARY KEY (filing_id, sid, col),
  FOREIGN KEY (filing_id, table_id)
    REFERENCES filing_tables (filing_id, table_id) ON DELETE CASCADE
);

-- Spec §5: a chunk's context is embedded, indexed and shown to the model but
-- never verified against. table_id is set only on pieces of a split table.
ALTER TABLE chunks ADD COLUMN context text NOT NULL DEFAULT '';
ALTER TABLE chunks ADD COLUMN table_id integer;

-- The lexical arm searches context too. Must stay spelled exactly like
-- api.retrieval._TSVECTOR (minus the ch. alias) or the planner falls back to
-- a sequential scan.
DROP INDEX chunks_text_fts;
CREATE INDEX chunks_text_fts ON chunks USING gin (to_tsvector('english', context || ' ' || text));
