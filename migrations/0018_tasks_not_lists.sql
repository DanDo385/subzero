-- 0018_tasks_not_lists.sql
-- Lists leave Subzero: shopping lists, errands and personal todos live in the
-- user's own apps through those apps' connectors. Project next steps stay.
-- The tasks VIEW over list_items becomes a real table, task items are copied
-- into it with their ids preserved, and the list tables are dropped.
--
-- Before applying, run `subzero export-lists --out DIR`: every non-task list
-- is exported to Markdown and JSON there, not migrated, and is gone once this
-- file has run.
--
-- Reverse (schema only; the dropped lists and their items cannot be restored,
-- which is what the export above is for):
--   DROP TABLE IF EXISTS tasks;
--   recreate lists and list_items exactly as in 0002_core.sql;
--   CREATE OR REPLACE VIEW tasks AS
--       SELECT li.id, li.list_id, l.project_id, li.text, li.status, li.priority,
--              li.due_date, li.notes, li.created_at, li.completed_at, li.metadata
--       FROM list_items li
--       JOIN lists l ON l.id = li.list_id
--       WHERE l.list_type = 'task';
--   DELETE FROM schema_migrations WHERE version = '0018_tasks_not_lists';

DROP VIEW IF EXISTS tasks;

CREATE TABLE IF NOT EXISTS tasks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   UUID REFERENCES projects(id) ON DELETE SET NULL,
    text         TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,                 -- normalized_sha256(text), the idempotency key
    status       TEXT NOT NULL DEFAULT 'open',    -- open | done | dropped | blocked
    priority     INTEGER NOT NULL DEFAULT 0,      -- higher is more urgent
    due_date     DATE,
    notes        TEXT,
    home         TEXT,                            -- projects/{slug} | lesson/{cat} | setup/{tool} | other
    source_provider TEXT REFERENCES providers(id),
    source_conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    provenance   pc_provenance NOT NULL DEFAULT 'user_statement',
    sensitivity  pc_sensitivity NOT NULL DEFAULT 'normal',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_tsv   TSVECTOR GENERATED ALWAYS AS
                   (to_tsvector('english', text || ' ' || coalesce(notes,''))) STORED
);

-- Same text twice is the same task: one row per project, or one row per
-- source conversation for a task with no project. Exactly the dedup
-- list_items enforced per list, scoped to where a task now lives.
CREATE UNIQUE INDEX IF NOT EXISTS tasks_project_dedup_key
    ON tasks (project_id, content_sha256) WHERE project_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS tasks_conversation_dedup_key
    ON tasks (source_conversation_id, content_sha256)
    WHERE project_id IS NULL AND source_conversation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS tasks_project_status_idx ON tasks (project_id, status);
CREATE INDEX IF NOT EXISTS tasks_search_idx ON tasks USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks (status, updated_at DESC);
DROP TRIGGER IF EXISTS tasks_touch ON tasks;
CREATE TRIGGER tasks_touch BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

-- Carry the task items over, ids preserved so existing references in
-- metadata, digests and tests keep working. A task list named {slug}-tasks
-- belongs to that project even though the old lists row never said so.
-- The earliest row wins a dedup collision.
INSERT INTO tasks (
    id, project_id, text, content_sha256, status, priority, due_date, notes, home,
    source_provider, source_conversation_id, provenance,
    created_at, updated_at, completed_at, metadata
)
SELECT li.id,
       coalesce(l.project_id, p.id),
       li.text, li.text_sha256, li.status, li.priority, li.due_date, li.notes,
       c.metadata->>'home',
       li.source_provider, li.source_conversation_id, li.provenance,
       li.created_at, li.updated_at, li.completed_at, li.metadata
FROM list_items li
JOIN lists l ON l.id = li.list_id
LEFT JOIN projects p ON l.project_id IS NULL AND l.slug = p.slug || '-tasks'
LEFT JOIN conversations c ON c.id = li.source_conversation_id
WHERE l.list_type = 'task'
ORDER BY li.created_at
ON CONFLICT DO NOTHING;

-- Queued renders of vault files whose rows are about to disappear.
DELETE FROM sync_jobs
WHERE job_type = 'write_list' AND status IN ('queued', 'approved', 'leased');

DROP TABLE IF EXISTS list_items;
DROP TABLE IF EXISTS lists;
