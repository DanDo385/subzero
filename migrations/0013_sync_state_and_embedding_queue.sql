-- Browser sync and background embedding.
--
-- provider_updated_at: when the provider last changed the conversation, as the
-- provider reports it. updated_at cannot carry this: the touch trigger sets it
-- to now() on every write. The extension compares the two to fetch only
-- conversations that changed since the server last saw them.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS provider_updated_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS conversations_provider_updated_idx
    ON conversations (provider, provider_updated_at DESC);

-- embedding_skipped: the embedder decided this message is not to be embedded
-- (privacy policy, nothing to embed). Keeps it out of the pending set for good.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS embedding_skipped BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS messages_embedding_pending_idx
    ON messages (created_at DESC)
    WHERE embedding IS NULL AND NOT embedding_skipped;
