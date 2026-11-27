-- 0024: weighted full-text vectors. Title-like text ranks at weight A and
-- tags at B, so an exact title beats a long note that repeats the words;
-- body stays at D. Queries pass explicit weights {D 0.005, C 0.2, B 0.4,
-- A 1.0} (see retrieval/sql.py): a title cover saturates at rank 1.0 while
-- body covers accumulate ~88xD each, so D must sit well under 1/88 for an
-- exact title to beat a thirty-fold repetition. Measured on PG 16.
--
-- Generated columns cannot hold this (their expressions forbid the
-- subquery that extracts the tags array), so search_tsv becomes a plain
-- column maintained by a trigger. Backfill rewrites each row once, which
-- fires the trigger; the old column (and its GIN index with it) is dropped
-- first. Never edit this file after it has applied: add a new migration.

CREATE OR REPLACE FUNCTION notes_search_tsv() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    tag_text text;
BEGIN
    SELECT coalesce(string_agg(t.value #>> '{}', ' '), '') INTO tag_text
    FROM jsonb_array_elements(coalesce(NEW.metadata->'tags', '[]'::jsonb)) AS t(value)
    WHERE jsonb_typeof(t.value) = 'string';
    NEW.search_tsv :=
        setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A')
        || setweight(to_tsvector('english', coalesce(tag_text, '')), 'B')
        || setweight(to_tsvector('english', coalesce(NEW.content, '')), 'D');
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION session_summaries_search_tsv() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv :=
        setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A')
        || setweight(to_tsvector('english', coalesce(NEW.summary, '')), 'D');
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION decisions_search_tsv() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv :=
        setweight(to_tsvector('english', coalesce(NEW.statement, '')), 'A')
        || setweight(to_tsvector('english', coalesce(NEW.rationale, '')), 'D');
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION memories_search_tsv() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv :=
        setweight(to_tsvector('english', coalesce(NEW.subject, '')), 'A')
        || setweight(to_tsvector('english', coalesce(NEW.content, '')), 'D');
    RETURN NEW;
END $$;

-- notes (drop the GIN index first: it depends on the generated column;
-- disable the touch trigger during backfill so updated_at keeps meaning
-- "last meaningful update", not "when the weights migration ran").
DROP INDEX IF EXISTS notes_search_idx;
ALTER TABLE notes DROP COLUMN search_tsv;
ALTER TABLE notes ADD COLUMN search_tsv TSVECTOR;
DROP TRIGGER IF EXISTS notes_search_tsv_trigger ON notes;
CREATE TRIGGER notes_search_tsv_trigger
    BEFORE INSERT OR UPDATE ON notes
    FOR EACH ROW EXECUTE FUNCTION notes_search_tsv();
ALTER TABLE notes DISABLE TRIGGER notes_touch;
UPDATE notes SET id = id;
ALTER TABLE notes ENABLE TRIGGER notes_touch;
CREATE INDEX IF NOT EXISTS notes_search_idx ON notes USING GIN (search_tsv);

-- session_summaries
DROP INDEX IF EXISTS session_summaries_search_idx;
ALTER TABLE session_summaries DROP COLUMN search_tsv;
ALTER TABLE session_summaries ADD COLUMN search_tsv TSVECTOR;
DROP TRIGGER IF EXISTS session_summaries_search_tsv_trigger ON session_summaries;
CREATE TRIGGER session_summaries_search_tsv_trigger
    BEFORE INSERT OR UPDATE ON session_summaries
    FOR EACH ROW EXECUTE FUNCTION session_summaries_search_tsv();
ALTER TABLE session_summaries DISABLE TRIGGER session_summaries_touch;
UPDATE session_summaries SET id = id;
ALTER TABLE session_summaries ENABLE TRIGGER session_summaries_touch;
CREATE INDEX IF NOT EXISTS session_summaries_search_idx ON session_summaries USING GIN (search_tsv);

-- decisions
DROP INDEX IF EXISTS decisions_search_idx;
ALTER TABLE decisions DROP COLUMN search_tsv;
ALTER TABLE decisions ADD COLUMN search_tsv TSVECTOR;
DROP TRIGGER IF EXISTS decisions_search_tsv_trigger ON decisions;
CREATE TRIGGER decisions_search_tsv_trigger
    BEFORE INSERT OR UPDATE ON decisions
    FOR EACH ROW EXECUTE FUNCTION decisions_search_tsv();
ALTER TABLE decisions DISABLE TRIGGER decisions_touch;
UPDATE decisions SET id = id;
ALTER TABLE decisions ENABLE TRIGGER decisions_touch;
CREATE INDEX IF NOT EXISTS decisions_search_idx ON decisions USING GIN (search_tsv);

-- memories
DROP INDEX IF EXISTS memories_search_idx;
ALTER TABLE memories DROP COLUMN search_tsv;
ALTER TABLE memories ADD COLUMN search_tsv TSVECTOR;
DROP TRIGGER IF EXISTS memories_search_tsv_trigger ON memories;
CREATE TRIGGER memories_search_tsv_trigger
    BEFORE INSERT OR UPDATE ON memories
    FOR EACH ROW EXECUTE FUNCTION memories_search_tsv();
ALTER TABLE memories DISABLE TRIGGER memories_touch;
UPDATE memories SET id = id;
ALTER TABLE memories ENABLE TRIGGER memories_touch;
CREATE INDEX IF NOT EXISTS memories_search_idx ON memories USING GIN (search_tsv);
