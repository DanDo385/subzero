-- Coding agents, the messages between them, and the work dispatched to them.
--
-- An agent session is one running harness (Claude Code in a worktree, a
-- firstmate crew member, a chat app) that registered itself through a hook or
-- a tool. It is a live-status row, not a transcript: transcripts stay in
-- conversations/messages, written by session_checkpoint as before.

CREATE TABLE IF NOT EXISTS agent_sessions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The harness's own session id (e.g. Claude Code's), so every hook call
    -- for one session lands on one row.
    external_id   TEXT UNIQUE,
    name          TEXT NOT NULL,
    harness       TEXT NOT NULL,
    machine       TEXT,
    cwd           TEXT,
    branch        TEXT,
    home          TEXT,
    status        TEXT NOT NULL DEFAULT 'working'
                  CHECK (status IN ('working', 'waiting', 'idle', 'done')),
    -- What it is doing now, or for 'waiting', what it needs from the person
    -- ("type the comptime function in src/list.zig:30").
    task          TEXT,
    job_id        UUID,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS agent_sessions_seen_idx ON agent_sessions (last_seen_at DESC);

CREATE TABLE IF NOT EXISTS dispatch_jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal        TEXT NOT NULL CHECK (length(goal) BETWEEN 1 AND 4000),
    mode        TEXT NOT NULL DEFAULT 'scout' CHECK (mode IN ('scout', 'ship')),
    home        TEXT,
    target      TEXT NOT NULL,
    routed_by   TEXT NOT NULL CHECK (routed_by IN ('sigil', 'jev', 'default')),
    confidence  REAL,
    status      TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'claimed', 'done', 'failed', 'cancelled')),
    claimed_by  UUID REFERENCES agent_sessions(id) ON DELETE SET NULL,
    result      TEXT,
    created_by  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dispatch_jobs_open_idx ON dispatch_jobs (target, created_at)
    WHERE status = 'queued';

CREATE TABLE IF NOT EXISTS agent_messages (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_session  UUID REFERENCES agent_sessions(id) ON DELETE SET NULL,
    from_label    TEXT NOT NULL,
    -- Exactly one addressee: a session, every live session in a home, or the
    -- person ('me').
    to_session    UUID REFERENCES agent_sessions(id) ON DELETE CASCADE,
    to_home       TEXT,
    to_me         BOOLEAN NOT NULL DEFAULT false,
    body          TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 4000),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at       TIMESTAMPTZ,
    CHECK (num_nonnulls(to_session, to_home) + to_me::int = 1)
);
CREATE INDEX IF NOT EXISTS agent_messages_to_session_idx ON agent_messages (to_session)
    WHERE read_at IS NULL;

-- Which home-wide messages each session has already been shown.
CREATE TABLE IF NOT EXISTS agent_message_reads (
    message_id  UUID NOT NULL REFERENCES agent_messages(id) ON DELETE CASCADE,
    session_id  UUID NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    read_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, session_id)
);

-- What happened, in order, for the board to animate.
CREATE TABLE IF NOT EXISTS agent_events (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT NOT NULL,
    session_id  UUID REFERENCES agent_sessions(id) ON DELETE CASCADE,
    job_id      UUID REFERENCES dispatch_jobs(id) ON DELETE CASCADE,
    detail      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS agent_events_at_idx ON agent_events (at DESC);
