-- 0025: heading-aware chunks for meaning search. A long note embedded as
-- one vector matches weakly and costs a full read; chunks split it at its
-- headings so each section is embedded under a "Title > Heading" prefix and
-- a hit names the section it matched. Whole-note vectors stay: keyword
-- search, memory_search and notes search keep using notes.embedding.
-- Chunk rows are derived data: notes.upsert and apply_vault_edit rebuild
-- them (delete + insert) whenever the body changes, and the background
-- embedding loop fills their vectors. Never edit this file after it has
-- applied: add a new migration.

CREATE TABLE note_chunks (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    note_id          UUID NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    heading_path     TEXT NOT NULL DEFAULT '',
    chunk_index      INTEGER NOT NULL DEFAULT 0,
    content          TEXT NOT NULL,
    content_sha256   TEXT NOT NULL,
    embedding        VECTOR(1536),
    embedding_skipped BOOLEAN NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS note_chunks_note_idx ON note_chunks (note_id, chunk_index);
CREATE INDEX IF NOT EXISTS note_chunks_embedding_idx
    ON note_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS note_chunks_embedding_pending_idx ON note_chunks (id)
    WHERE embedding IS NULL AND NOT embedding_skipped;
