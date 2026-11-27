-- 0026: live session lifecycle. Conversations created by session_open stay
-- open until session_close (or the sweeper) marks them ended. ended_at null
-- means live. Never edit this file after it has applied: add a new migration.

ALTER TABLE conversations ADD COLUMN ended_at TIMESTAMPTZ;
ALTER TABLE conversations ADD COLUMN end_reason TEXT;
CREATE INDEX IF NOT EXISTS conversations_live_idx ON conversations (provider, updated_at DESC)
    WHERE ended_at IS NULL;
