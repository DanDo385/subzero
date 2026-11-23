-- Memories and session summaries join the background embedding refresh, as
-- messages (0013) and notes (0014) did. Rows saved while embeddings were off,
-- or while the provider was out of credit, had no way to get a vector later.
-- embedding_skipped keeps a row the privacy policy forbids out of the pending
-- set for good, so the loop never rescans what it may not send.
ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding_skipped BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS memories_embedding_pending_idx
    ON memories (updated_at DESC)
    WHERE embedding IS NULL AND NOT embedding_skipped AND status = 'active';

ALTER TABLE session_summaries ADD COLUMN IF NOT EXISTS embedding_skipped BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS session_summaries_embedding_pending_idx
    ON session_summaries (updated_at DESC)
    WHERE embedding IS NULL AND NOT embedding_skipped;
