-- 0021_dispatch_exec.sql
-- dispatch_jobs learns to carry executable intent next to goal-shaped work:
-- kind='command' (one command for the machine's runner) and kind='session'
-- (an interactive session the runner opens, keep_open). The server still
-- executes nothing; it stores the intent and the results, and a runner on the
-- user's machine, holding a paired device token, does the executing.
--
-- Every column here is additive (nullable or with a default), so a server
-- still running the old code works against the new schema: it reads and
-- writes the columns it knows and leaves the rest at their defaults.
--
-- mode keeps its meaning for kind='goal' only. An executable job has no
-- scout/ship distinction; its mode stays at the column default and is
-- ignored for the new kinds.
--
-- Reverse:
--   ALTER TABLE dispatch_jobs
--       DROP CONSTRAINT IF EXISTS dispatch_jobs_timeout_range,
--       DROP CONSTRAINT IF EXISTS dispatch_jobs_command_needs_command_kind,
--       DROP COLUMN IF EXISTS kind,
--       DROP COLUMN IF EXISTS space,
--       DROP COLUMN IF EXISTS worktree,
--       DROP COLUMN IF EXISTS cwd,
--       DROP COLUMN IF EXISTS command,
--       DROP COLUMN IF EXISTS timeout_s,
--       DROP COLUMN IF EXISTS keep_open,
--       DROP COLUMN IF EXISTS exit_code,
--       DROP COLUMN IF EXISTS output,
--       DROP COLUMN IF EXISTS output_truncated;
--   DELETE FROM schema_migrations WHERE version = '0021_dispatch_exec';

ALTER TABLE dispatch_jobs ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'goal'
    CHECK (kind IN ('goal', 'command', 'session'));
-- herdr workspace id or label. Opaque to the server: it cannot ask herdr
-- whether the label exists, so it stores it and the runner finds out.
ALTER TABLE dispatch_jobs ADD COLUMN IF NOT EXISTS space TEXT;
-- What the caller asked for, as written ("subzero/cli-exec", "~/Code/x", ...).
ALTER TABLE dispatch_jobs ADD COLUMN IF NOT EXISTS worktree TEXT;
-- Resolved server-side against SUBZERO_EXEC_ROOTS (agents/exec_paths.py);
-- the runner validates it again before anything executes.
ALTER TABLE dispatch_jobs ADD COLUMN IF NOT EXISTS cwd TEXT;
-- kind='command' only, enforced by the CHECK below.
ALTER TABLE dispatch_jobs ADD COLUMN IF NOT EXISTS command TEXT
    CHECK (command IS NULL OR length(command) BETWEEN 1 AND 4000);
ALTER TABLE dispatch_jobs ADD COLUMN IF NOT EXISTS timeout_s INTEGER;
ALTER TABLE dispatch_jobs ADD COLUMN IF NOT EXISTS keep_open BOOLEAN NOT NULL DEFAULT false;
-- What came back from the runner. output is user content: a tail only,
-- capped by the service, never sent to a classifier, embedder or any
-- external call, and returned only to the caller.
ALTER TABLE dispatch_jobs ADD COLUMN IF NOT EXISTS exit_code INTEGER;
ALTER TABLE dispatch_jobs ADD COLUMN IF NOT EXISTS output TEXT;
ALTER TABLE dispatch_jobs ADD COLUMN IF NOT EXISTS output_truncated BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE dispatch_jobs DROP CONSTRAINT IF EXISTS dispatch_jobs_command_needs_command_kind;
ALTER TABLE dispatch_jobs ADD CONSTRAINT dispatch_jobs_command_needs_command_kind
    CHECK (command IS NULL OR kind = 'command');
ALTER TABLE dispatch_jobs DROP CONSTRAINT IF EXISTS dispatch_jobs_timeout_range;
ALTER TABLE dispatch_jobs ADD CONSTRAINT dispatch_jobs_timeout_range
    CHECK (timeout_s IS NULL OR timeout_s BETWEEN 1 AND 1800);
