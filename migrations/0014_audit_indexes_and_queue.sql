-- Fixes from the DDIA audit (docs/ddia-audit.md). Each statement answers a
-- measured production cost or a verified correctness bug, and the finding it
-- closes is named in the comment.

-- (H5) The extension's incremental sync orders by provider_updated_at DESC
-- NULLS LAST. The index 0013 built for it is DESC, which in Postgres implies
-- NULLS FIRST, so it cannot serve that ordering and the planner seq-scans
-- conversations on every call. Measured on the production database: 1,041 ms
-- and 1,871 buffer hits, against 0.361 ms and 60 buffer hits once an index can
-- serve the ordering. At 7,624 calls this was the most expensive statement in
-- the database. The partial predicate matches the query's own filter.
CREATE INDEX IF NOT EXISTS conversations_provider_sync_idx
    ON conversations (provider, provider_updated_at DESC NULLS LAST)
    WHERE provider_conversation_id IS NOT NULL;

-- (H3) One open job per vault path keeps the queue one-deep per file, but the
-- index covered 'leased' as well while enqueue() only supersedes 'queued' and
-- 'approved'. A write queued while the worker held a lease therefore hit
-- ON CONFLICT DO NOTHING and vanished: the caller still recorded the render as
-- done, so the content was never written and nothing showed it was missing.
-- Excluding 'leased' lets the newer job wait behind the one in flight.
DROP INDEX IF EXISTS sync_jobs_open_path_key;
CREATE UNIQUE INDEX IF NOT EXISTS sync_jobs_open_path_key
    ON sync_jobs (vault_path)
    WHERE status IN ('queued', 'approved');

-- (H4) A write_list job carried no expected_sha256, so the worker's hash check
-- was skipped and a list render overwrote a human edit outright. lists now
-- remembers what the worker last wrote, exactly as notes and mocs already do.
ALTER TABLE lists ADD COLUMN IF NOT EXISTS vault_synced_sha256 TEXT;

-- A note's vector becomes derived data, written by the background refresh loop
-- rather than by an embedding call inside the writer's transaction. That call
-- was a network round trip of up to 30 s holding row locks and a pooled
-- connection. embedding_skipped keeps a note the privacy policy forbids out of
-- the pending set for good, the way 0013 does for messages, so the loop never
-- rescans rows it is not allowed to embed.
ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding_skipped BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS notes_embedding_pending_idx
    ON notes (updated_at DESC)
    WHERE embedding IS NULL AND NOT embedding_skipped;

-- Both of these duplicate a unique index on the same columns in the same order,
-- so the planner never chooses them (idx_scan = 0 across two months of
-- production traffic) while every insert maintains them. messages_conv_idx
-- repeats messages_conv_seq_key (0002), and bridge_messages_session_idx repeats
-- the UNIQUE (session_id, sequence) constraint (0010).
DROP INDEX IF EXISTS messages_conv_idx;
DROP INDEX IF EXISTS bridge_messages_session_idx;

-- The background classifier used to rediscover its work every tick by
-- anti-joining classification_events against every imported conversation, and
-- nothing claimed a row, so two instances would classify the same conversation
-- twice. It now claims from reclassification_queue with FOR UPDATE SKIP
-- LOCKED. Seed the queue with what that scan would have found so nothing
-- already waiting is dropped by the change, and index the claim.
INSERT INTO reclassification_queue (object_type, object_id, reason)
SELECT 'checkpoint', c.id, 'never_classified'
FROM conversations c
WHERE c.imported
  AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id)
  AND NOT EXISTS (
        SELECT 1 FROM classification_events e
        WHERE e.object_type = 'checkpoint' AND e.object_id = c.id AND NOT e.degraded)
ON CONFLICT (object_type, object_id) DO NOTHING;

CREATE INDEX IF NOT EXISTS reclassification_queue_due_idx
    ON reclassification_queue (next_attempt_at, created_at);

-- One row per background loop that must run in exactly one process. Which
-- server ran them was a deployment convention before this: a setting said
-- "off" on the serverless side and nothing checked it. A holder that dies
-- stops renewing, the row expires, and another process picks the work up.
CREATE TABLE IF NOT EXISTS background_leases (
    name        TEXT PRIMARY KEY,
    holder      TEXT NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
